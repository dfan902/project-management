# app.py
# Streamlit + Supabase internal project tracker
# Full version with:
# - Home page project cards
# - Add project from Home
# - Project page timeline + updates
# - Project page task actions via top buttons
# - Real team field on tasks
# - Settings for users + export only

from datetime import date, timedelta
from io import BytesIO
import re

import pandas as pd
import plotly.express as px
import streamlit as st
from supabase import Client, create_client

STATUSES = ["Not Started", "In Progress", "Blocked", "Done"]
PROJECT_STATUSES = ["Not Started", "In Progress", "Done"]


# -------------------------
# Supabase connection
# -------------------------

def get_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


# -------------------------
# Data helpers
# -------------------------

def fetch_users(supabase: Client) -> pd.DataFrame:
    response = supabase.table("users").select("id, name").order("name").execute()
    return pd.DataFrame(response.data or [])


def fetch_projects(supabase: Client) -> pd.DataFrame:
    response = (
        supabase.table("projects")
        .select("id, name, description, due_date, status")
        .order("name")
        .execute()
    )
    return pd.DataFrame(response.data or [])


def fetch_tasks(supabase: Client) -> pd.DataFrame:
    response = (
        supabase.table("tasks")
        .select(
            """
            id,
            title,
            team,
            progress_percent,
            due_date,
            status,
            latest_update,
            notes,
            updated_at,
            project_id,
            owner_primary_id,
            owner_secondary_id,
            projects(name),
            owner_primary:users!tasks_owner_primary_id_fkey(name),
            owner_secondary:users!tasks_owner_secondary_id_fkey(name)
            """
        )
        .order("due_date", desc=False)
        .execute()
    )

    rows = []
    for item in (response.data or []):
        rows.append(
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "team": item.get("team"),
                "project": (item.get("projects") or {}).get("name"),
                "owner_primary": (item.get("owner_primary") or {}).get("name"),
                "owner_secondary": (item.get("owner_secondary") or {}).get("name"),
                "progress_percent": item.get("progress_percent"),
                "due_date": item.get("due_date"),
                "status": item.get("status"),
                "latest_update": item.get("latest_update"),
                "notes": item.get("notes"),
                "updated_at": item.get("updated_at"),
                "project_id": item.get("project_id"),
                "owner_primary_id": item.get("owner_primary_id"),
                "owner_secondary_id": item.get("owner_secondary_id"),
            }
        )

    return pd.DataFrame(rows)


def add_user(supabase: Client, name: str) -> None:
    supabase.table("users").insert({"name": name}).execute()


def add_project(supabase: Client, name: str, description: str, due_date: str | None, status: str) -> None:
    supabase.table("projects").insert(
        {
            "name": name,
            "description": description,
            "due_date": due_date,
            "status": status,
        }
    ).execute()


def update_project(
    supabase: Client,
    project_id: int,
    name: str,
    description: str,
    due_date: str | None,
    status: str,
) -> None:
    supabase.table("projects").update(
        {
            "name": name,
            "description": description,
            "due_date": due_date,
            "status": status,
        }
    ).eq("id", project_id).execute()


def delete_project(supabase: Client, project_id: int) -> None:
    supabase.table("tasks").update({"project_id": None}).eq("project_id", project_id).execute()
    supabase.table("projects").delete().eq("id", project_id).execute()


def add_task(
    supabase: Client,
    title: str,
    team: str,
    project_id: int | None,
    owner_primary_id: int | None,
    owner_secondary_id: int | None,
    progress_percent: int,
    due_date: str | None,
    status: str,
    latest_update: str,
    notes: str,
) -> None:
    supabase.table("tasks").insert(
        {
            "title": title,
            "team": team,
            "project_id": project_id,
            "owner_primary_id": owner_primary_id,
            "owner_secondary_id": owner_secondary_id,
            "progress_percent": progress_percent,
            "due_date": due_date,
            "status": status,
            "latest_update": latest_update,
            "notes": notes,
        }
    ).execute()


def update_task_full(
    supabase: Client,
    task_id: int,
    title: str,
    team: str,
    project_id: int | None,
    owner_primary_id: int | None,
    owner_secondary_id: int | None,
    progress_percent: int,
    due_date: str | None,
    status: str,
    latest_update: str,
    notes: str,
) -> None:
    supabase.table("tasks").update(
        {
            "title": title,
            "team": team,
            "project_id": project_id,
            "owner_primary_id": owner_primary_id,
            "owner_secondary_id": owner_secondary_id,
            "progress_percent": progress_percent,
            "due_date": due_date,
            "status": status,
            "latest_update": latest_update,
            "notes": notes,
        }
    ).eq("id", task_id).execute()


def delete_task(supabase: Client, task_id: int) -> None:
    supabase.table("tasks").delete().eq("id", task_id).execute()


# -------------------------
# Export helpers
# -------------------------

def make_safe_sheet_name(name: str, used_names: set[str]) -> str:
    safe = re.sub(r"[\\/*?:\[\]]", "_", str(name)).strip()
    safe = safe.strip("'")
    if not safe:
        safe = "Project"
    safe = safe[:31]

    original = safe
    counter = 2
    while safe in used_names:
        suffix = f"_{counter}"
        safe = f"{original[:31-len(suffix)]}{suffix}"
        counter += 1

    used_names.add(safe)
    return safe


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    export_df = df.copy()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        used_names = set()
        summary_name = make_safe_sheet_name("All Visible Tasks", used_names)
        export_df.to_excel(writer, index=False, sheet_name=summary_name)

        working_df = export_df[export_df["project"].notna()].copy()
        if "status" in working_df.columns:
            working_df = working_df[working_df["status"] != "Done"]

        if not working_df.empty:
            for project_name in sorted(working_df["project"].dropna().unique()):
                project_df = working_df[working_df["project"] == project_name].copy()
                safe_sheet_name = make_safe_sheet_name(project_name, used_names)
                project_df.to_excel(writer, index=False, sheet_name=safe_sheet_name)

    output.seek(0)
    return output.getvalue()


# -------------------------
# Timeline helpers
# -------------------------

def prepare_project_timeline_df(project_tasks: pd.DataFrame) -> pd.DataFrame:
    if project_tasks.empty:
        return pd.DataFrame()

    df = project_tasks.copy()
    df["due_date"] = pd.to_datetime(df["due_date"], errors="coerce")
    today = pd.Timestamp(date.today())

    def infer_start(row):
        due = row["due_date"]
        status = row["status"]

        if pd.isna(due):
            return today
        if status == "Done":
            return due - timedelta(days=7)
        if status == "In Progress":
            return due - timedelta(days=10)
        if status == "Blocked":
            return due - timedelta(days=5)
        return due - timedelta(days=14)

    df["start_date"] = df.apply(infer_start, axis=1)
    df["task_label"] = df["title"]
    return df


def render_project_timeline(project_tasks: pd.DataFrame) -> None:
    st.markdown("#### Timeline")

    timeline_df = prepare_project_timeline_df(project_tasks)
    if timeline_df.empty:
        st.info("No tasks available for the timeline yet.")
        return

    chart_df = timeline_df.dropna(subset=["due_date"]).copy()
    if chart_df.empty:
        st.info("Tasks need due dates before they can appear on the timeline.")
        return

    fig = px.timeline(
        chart_df,
        x_start="start_date",
        x_end="due_date",
        y="task_label",
        color="task_label",
        hover_data={
            "team": True,
            "owner_primary": True,
            "owner_secondary": True,
            "status": True,
            "due_date": True,
            "start_date": True,
            "task_label": False,
        },
    )

    fig.update_yaxes(autorange="reversed")
    fig.update_layout(
        height=max(420, len(chart_df) * 45),
        margin=dict(l=20, r=20, t=30, b=20),
        xaxis_title="Timeline",
        yaxis_title="Task",
        legend_title="Task",
    )

    st.plotly_chart(fig, use_container_width=True)


# -------------------------
# UI helpers
# -------------------------

def render_top_bar() -> None:
    top_left, top_right = st.columns([6, 1])
    with top_left:
        st.title("Project Tracker")
        st.caption("Internal project visibility dashboard")
    with top_right:
        if st.button("⚙️", help="Open settings", use_container_width=True):
            st.session_state["page"] = "Settings"
            st.rerun()


def render_home(supabase: Client, projects_df: pd.DataFrame, tasks_df: pd.DataFrame) -> None:
    top_left, top_right = st.columns([4, 1])

    with top_left:
        st.subheader("Projects")
        st.write("Select a project to review its details.")

    with top_right:
        with st.popover("＋ Add Project", use_container_width=True):
            with st.form("home_add_project_form", clear_on_submit=True):
                project_name = st.text_input("Project name")
                project_description = st.text_area("Description")
                project_due_date = st.date_input("Project due date", value=None)
                project_status = st.selectbox("Project status", PROJECT_STATUSES)
                submitted = st.form_submit_button("Create project")

                if submitted and project_name.strip():
                    add_project(
                        supabase,
                        project_name.strip(),
                        project_description.strip(),
                        project_due_date.isoformat() if project_due_date else None,
                        project_status,
                    )
                    st.success("Project created.")
                    st.rerun()

    if projects_df.empty:
        st.info("No projects available yet.")
        return

    project_cards = []
    for _, project in projects_df.iterrows():
        project_name = project.get("name")
        project_tasks = tasks_df[tasks_df["project"] == project_name].copy() if not tasks_df.empty else pd.DataFrame()
        total_tasks = len(project_tasks)
        blocked_tasks = len(project_tasks[project_tasks["status"] == "Blocked"]) if not project_tasks.empty else 0

        overdue_tasks = 0
        if not project_tasks.empty and "due_date" in project_tasks.columns:
            due_dates = pd.to_datetime(project_tasks["due_date"], errors="coerce")
            overdue_tasks = int(((due_dates < pd.Timestamp(date.today())) & (project_tasks["status"] != "Done")).sum())

        project_cards.append(
            {
                "id": project.get("id"),
                "name": project_name,
                "status": project.get("status"),
                "due_date": project.get("due_date"),
                "total_tasks": total_tasks,
                "blocked_tasks": blocked_tasks,
                "overdue_tasks": overdue_tasks,
            }
        )

    for start in range(0, len(project_cards), 3):
        cols = st.columns(3)
        for col, card in zip(cols, project_cards[start:start + 3]):
            with col:
                with st.container(border=True):
                    st.markdown(f"### {card['name']}")
                    st.write(f"**Status:** {card['status'] or '—'}")
                    st.write(f"**Due date:** {card['due_date'] or '—'}")
                    st.write(f"**Tasks:** {card['total_tasks']}")
                    st.write(f"**Blocked:** {card['blocked_tasks']}")
                    st.write(f"**Overdue:** {card['overdue_tasks']}")
                    if st.button("Open project", key=f"open_project_{card['id']}", use_container_width=True):
                        st.session_state["selected_project_id"] = int(card["id"])
                        st.session_state["page"] = "Project"
                        st.session_state["project_edit_mode"] = False
                        st.session_state["task_mode"] = None
                        st.rerun()


def render_project_updates(project_tasks: pd.DataFrame) -> None:
    st.markdown("#### Updates")

    if project_tasks.empty:
        st.info("No task updates available.")
        return

    filter_col1, filter_col2, filter_col3 = st.columns(3)

    team_options = ["All"] + sorted(project_tasks["team"].dropna().unique().tolist()) if "team" in project_tasks.columns else ["All"]
    task_options = ["All"] + sorted(project_tasks["title"].dropna().unique().tolist())
    status_options = ["All"] + STATUSES

    with filter_col1:
        selected_team = st.selectbox("Filter by team", team_options, key="project_team_filter")
    with filter_col2:
        selected_task = st.selectbox("Filter by task", task_options, key="project_task_filter")
    with filter_col3:
        selected_status = st.selectbox("Filter by status", status_options, key="project_status_filter")

    filtered = project_tasks.copy()

    if selected_team != "All":
        filtered = filtered[filtered["team"] == selected_team]
    if selected_task != "All":
        filtered = filtered[filtered["title"] == selected_task]
    if selected_status != "All":
        filtered = filtered[filtered["status"] == selected_status]

    if filtered.empty:
        st.info("No matching updates found.")
        return

    display_df = filtered[
        [
            "team",
            "title",
            "owner_primary",
            "due_date",
            "status",
            "latest_update",
            "notes",
        ]
    ].copy()

    display_df = display_df.rename(
        columns={
            "team": "Team",
            "title": "Task",
            "owner_primary": "Owner",
            "due_date": "Due Date",
            "status": "Status",
            "latest_update": "Update",
            "notes": "Notes",
        }
    )

    st.dataframe(display_df, use_container_width=True, hide_index=True)


def render_project_page(
    supabase: Client,
    projects_df: pd.DataFrame,
    tasks_df: pd.DataFrame,
    users_df: pd.DataFrame,
) -> None:
    project_id = st.session_state.get("selected_project_id")

    if not project_id or projects_df.empty:
        st.info("Choose a project from the home page.")
        return

    match = projects_df[projects_df["id"] == project_id]
    if match.empty:
        st.warning("This project could not be found.")
        return

    project_row = match.iloc[0]
    project_name = project_row["name"]
    project_tasks = tasks_df[tasks_df["project"] == project_name].copy() if not tasks_df.empty else pd.DataFrame()
    user_map = {row["name"]: row["id"] for _, row in users_df.iterrows()} if not users_df.empty else {}

    back_col, title_col, action_col = st.columns([1, 4, 1])

    with back_col:
        if st.button("← Back", use_container_width=True):
            st.session_state["page"] = "Home"
            st.session_state["task_mode"] = None
            st.session_state["project_edit_mode"] = False
            st.rerun()

    with title_col:
        st.subheader(project_name)
        st.caption(project_row.get("description", "") or "Project detail view")

    with action_col:
        if st.button("⚙️ Edit", use_container_width=True):
            st.session_state["project_edit_mode"] = not st.session_state.get("project_edit_mode", False)
            st.rerun()

    info1, info2, info3, info4 = st.columns(4)
    info1.metric("Tasks", len(project_tasks))
    info2.metric("Blocked", len(project_tasks[project_tasks["status"] == "Blocked"]) if not project_tasks.empty else 0)
    info3.metric("Done", len(project_tasks[project_tasks["status"] == "Done"]) if not project_tasks.empty else 0)
    info4.metric("Due date", project_row["due_date"] or "—")

    st.divider()

    task_action_col1, task_action_col2, task_action_col3 = st.columns(3)
    with task_action_col1:
        if st.button("＋ Add Task", use_container_width=True):
            st.session_state["task_mode"] = "add"
            st.rerun()
    with task_action_col2:
        if st.button("✏️ Edit Task", use_container_width=True):
            st.session_state["task_mode"] = "edit"
            st.rerun()
    with task_action_col3:
        if st.button("🗑️ Delete Task", use_container_width=True):
            st.session_state["task_mode"] = "delete"
            st.rerun()

    if st.session_state.get("project_edit_mode", False):
        st.divider()
        st.markdown("#### Edit project")

        existing_project_due = pd.to_datetime(project_row["due_date"], errors="coerce")

        with st.form("project_page_edit_project_form"):
            edit_project_name = st.text_input("Project name", value=project_row["name"] or "")
            edit_project_description = st.text_area("Description", value=project_row.get("description", "") or "")
            edit_project_due = st.date_input(
                "Project due date",
                value=existing_project_due.date() if pd.notna(existing_project_due) else None,
            )
            edit_project_status = st.selectbox(
                "Project status",
                PROJECT_STATUSES,
                index=PROJECT_STATUSES.index(project_row["status"]) if project_row["status"] in PROJECT_STATUSES else 0,
            )
            confirm_delete_project = st.checkbox("I confirm I want to permanently delete this project")

            save_project = st.form_submit_button("Save project changes")
            delete_project_btn = st.form_submit_button("Delete project")

            if save_project and edit_project_name.strip():
                update_project(
                    supabase,
                    int(project_row["id"]),
                    edit_project_name.strip(),
                    edit_project_description.strip(),
                    edit_project_due.isoformat() if edit_project_due else None,
                    edit_project_status,
                )
                st.success("Project updated.")
                st.rerun()

            if delete_project_btn:
                if not confirm_delete_project:
                    st.error("Please check the confirmation box before deleting this project.")
                else:
                    delete_project(supabase, int(project_row["id"]))
                    st.session_state["page"] = "Home"
                    st.session_state["selected_project_id"] = None
                    st.session_state["task_mode"] = None
                    st.session_state["project_edit_mode"] = False
                    st.success("Project deleted.")
                    st.rerun()

    task_mode = st.session_state.get("task_mode")

    if task_mode == "add":
        st.divider()
        st.markdown("#### Add task to this project")

        with st.form("project_page_add_task_form", clear_on_submit=True):
            title = st.text_input("Task title")
            team = st.text_input("Team")
            selected_primary = st.selectbox("Primary owner", list(user_map.keys()) if user_map else [None])
            selected_secondary = st.selectbox("Secondary owner", [None] + list(user_map.keys()), format_func=lambda x: x or "None")
            progress_percent = st.slider("Progress %", 0, 100, 0)
            due_date = st.date_input("Due date", value=None)
            status = st.selectbox("Status", STATUSES)
            latest_update = st.text_area("Latest progress update")
            notes = st.text_area("Notes")

            submitted = st.form_submit_button("Create task")
            if submitted and title.strip() and selected_primary:
                add_task(
                    supabase,
                    title.strip(),
                    team.strip(),
                    int(project_row["id"]),
                    user_map.get(selected_primary),
                    user_map.get(selected_secondary) if selected_secondary else None,
                    progress_percent,
                    due_date.isoformat() if due_date else None,
                    status,
                    latest_update.strip(),
                    notes.strip(),
                )
                st.success("Task created.")
                st.rerun()

    if task_mode == "edit":
        st.divider()
        st.markdown("#### Edit task")

        if project_tasks.empty:
            st.info("No tasks available in this project.")
        else:
            task_options = {f"#{row['id']} - {row['title']}": row for _, row in project_tasks.iterrows()}
            selected_label = st.selectbox("Select task", list(task_options.keys()), key="project_task_editor")
            selected_task = task_options[selected_label]

            current_primary_name = next((name for name, uid in user_map.items() if uid == selected_task["owner_primary_id"]), None)
            current_secondary_name = next((name for name, uid in user_map.items() if uid == selected_task["owner_secondary_id"]), None)

            with st.form("project_page_edit_task_form"):
                edit_title = st.text_input("Task title", value=selected_task["title"] or "")
                edit_team = st.text_input("Team", value=selected_task["team"] or "")
                owner_choices = list(user_map.keys()) if user_map else [None]
                edit_primary_owner = st.selectbox(
                    "Primary owner",
                    owner_choices,
                    index=owner_choices.index(current_primary_name) if current_primary_name in owner_choices else 0,
                )
                secondary_choices = [None] + list(user_map.keys())
                edit_secondary_owner = st.selectbox(
                    "Secondary owner",
                    secondary_choices,
                    index=secondary_choices.index(current_secondary_name) if current_secondary_name in secondary_choices else 0,
                    format_func=lambda x: x or "None",
                )
                edit_progress = st.slider("Progress %", 0, 100, int(selected_task["progress_percent"] or 0))
                existing_due = pd.to_datetime(selected_task["due_date"], errors="coerce")
                edit_due = st.date_input("Due date", value=existing_due.date() if pd.notna(existing_due) else None)
                edit_status = st.selectbox(
                    "Status",
                    STATUSES,
                    index=STATUSES.index(selected_task["status"]) if selected_task["status"] in STATUSES else 0,
                )
                edit_latest_update = st.text_area("Latest progress update", value=selected_task["latest_update"] or "")
                edit_notes = st.text_area("Notes", value=selected_task["notes"] or "")

                save_task = st.form_submit_button("Save task changes")
                if save_task and edit_title.strip() and edit_primary_owner:
                    update_task_full(
                        supabase,
                        int(selected_task["id"]),
                        edit_title.strip(),
                        edit_team.strip(),
                        int(project_row["id"]),
                        user_map.get(edit_primary_owner),
                        user_map.get(edit_secondary_owner) if edit_secondary_owner else None,
                        edit_progress,
                        edit_due.isoformat() if edit_due else None,
                        edit_status,
                        edit_latest_update.strip(),
                        edit_notes.strip(),
                    )
                    st.success("Task updated.")
                    st.rerun()

    if task_mode == "delete":
        st.divider()
        st.markdown("#### Delete task")

        if project_tasks.empty:
            st.info("No tasks available in this project.")
        else:
            task_options = {f"#{row['id']} - {row['title']}": row for _, row in project_tasks.iterrows()}
            selected_label = st.selectbox("Select task", list(task_options.keys()), key="project_task_delete")
            selected_task = task_options[selected_label]

            with st.form("project_page_delete_task_form"):
                st.write(f"Task: **{selected_task['title']}**")
                confirm_delete_task = st.checkbox("I confirm I want to permanently delete this task")
                delete_task_btn = st.form_submit_button("Delete task")

                if delete_task_btn:
                    if not confirm_delete_task:
                        st.error("Please check the confirmation box before deleting this task.")
                    else:
                        delete_task(supabase, int(selected_task["id"]))
                        st.success("Task deleted.")
                        st.rerun()

    st.divider()
    render_project_timeline(project_tasks)

    st.divider()
    render_project_updates(project_tasks)


def render_settings(supabase: Client, users_df: pd.DataFrame, tasks_df: pd.DataFrame) -> None:
    back_col, title_col = st.columns([1, 5])
    with back_col:
        if st.button("← Home", use_container_width=True):
            st.session_state["page"] = "Home"
            st.rerun()
    with title_col:
        st.subheader("Settings")
        st.caption("Manage users and exports")

    tab_users, tab_exports = st.tabs(["Users", "Export"])

    with tab_users:
        st.markdown("#### Add user")
        with st.form("add_user_form", clear_on_submit=True):
            user_name = st.text_input("User name")
            submitted = st.form_submit_button("Add user")
            if submitted and user_name.strip():
                add_user(supabase, user_name.strip())
                st.success("User added.")
                st.rerun()

        st.markdown("#### Current users")
        if users_df.empty:
            st.info("No users available.")
        else:
            st.dataframe(users_df, use_container_width=True, hide_index=True)

    with tab_exports:
        st.markdown("#### Export visible task data")
        if tasks_df.empty:
            st.info("No task data available to export.")
        else:
            export_df = tasks_df[
                [
                    "id",
                    "project",
                    "team",
                    "title",
                    "owner_primary",
                    "owner_secondary",
                    "progress_percent",
                    "due_date",
                    "status",
                    "latest_update",
                    "notes",
                    "updated_at",
                ]
            ].copy()
            excel_bytes = dataframe_to_excel_bytes(export_df)
            st.download_button(
                label="Export to Excel",
                data=excel_bytes,
                file_name="project_tracker_projects_export.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            st.dataframe(export_df, use_container_width=True, hide_index=True)


# -------------------------
# Main app
# -------------------------

def main() -> None:
    st.set_page_config(page_title="Internal Project Tracker", layout="wide")

    if "page" not in st.session_state:
        st.session_state["page"] = "Home"
    if "task_mode" not in st.session_state:
        st.session_state["task_mode"] = None
    if "project_edit_mode" not in st.session_state:
        st.session_state["project_edit_mode"] = False

    supabase = get_supabase()
    users_df = fetch_users(supabase)
    projects_df = fetch_projects(supabase)
    tasks_df = fetch_tasks(supabase)

    render_top_bar()
    st.divider()

    page = st.session_state.get("page", "Home")
    if page == "Settings":
        render_settings(supabase, users_df, tasks_df)
    elif page == "Project":
        render_project_page(supabase, projects_df, tasks_df, users_df)
    else:
        render_home(supabase, projects_df, tasks_df)


if __name__ == "__main__":
    main()


# requirements.txt
# streamlit
# pandas
# supabase
# openpyxl
# plotly


# schema.sql
# Run this in the Supabase SQL editor
#
# create table if not exists public.users (
#   id bigint generated by default as identity primary key,
#   name text not null unique
# );
#
# create table if not exists public.projects (
#   id bigint generated by default as identity primary key,
#   name text not null unique,
#   description text,
#   due_date date,
#   status text not null default 'In Progress'
# );
#
# create table if not exists public.tasks (
#   id bigint generated by default as identity primary key,
#   title text not null,
#   team text,
#   project_id bigint references public.projects(id) on delete set null,
#   owner_primary_id bigint references public.users(id) on delete set null,
#   owner_secondary_id bigint references public.users(id) on delete set null,
#   progress_percent integer not null default 0 check (progress_percent >= 0 and progress_percent <= 100),
#   due_date date,
#   status text not null default 'Not Started',
#   latest_update text,
#   notes text,
#   updated_at timestamptz not null default now()
# );
#
# If your tasks table already exists, run this too:
# alter table public.tasks add column if not exists team text;
