# app.py
# Streamlit + Supabase version of a lightweight internal project tracker
# Run locally with: streamlit run app.py

from datetime import date
import pandas as pd
import streamlit as st
from io import BytesIO
import re
from supabase import create_client, Client

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


def add_task(
    supabase: Client,
    title: str,
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


def delete_task(supabase: Client, task_id: int) -> None:
    supabase.table("tasks").delete().eq("id", task_id).execute()


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
    # Clear project links from tasks first so the project can be removed safely
    supabase.table("tasks").update({"project_id": None}).eq("project_id", project_id).execute()
    supabase.table("projects").delete().eq("id", project_id).execute()


def update_task(
    supabase: Client,
    task_id: int,
    progress_percent: int,
    due_date: str | None,
    status: str,
    latest_update: str,
    notes: str,
) -> None:
    supabase.table("tasks").update(
        {
            "progress_percent": progress_percent,
            "due_date": due_date,
            "status": status,
            "latest_update": latest_update,
            "notes": notes,
        }
    ).eq("id", task_id).execute()


# -------------------------
# UI helpers
# -------------------------

def make_safe_sheet_name(name: str, used_names: set[str]) -> str:
    safe = re.sub(r"[\/*?:\[\]]", "_", str(name)).strip()
    safe = safe.strip("'")
    if not safe:
        safe = "Project"

    # Excel sheet names max out at 31 characters
    safe = safe[:31]

    # Ensure uniqueness after sanitizing/truncating
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

        # Summary sheet
        summary_name = make_safe_sheet_name("All Visible Tasks", used_names)
        export_df.to_excel(writer, index=False, sheet_name=summary_name)

        # Keep only non-completed tasks for separate project sheets
        working_df = export_df[export_df["project"].notna()].copy()
        if "status" in working_df.columns:
            working_df = working_df[working_df["status"] != "Done"]

        # One sheet per active project
        if not working_df.empty:
            for project_name in sorted(working_df["project"].dropna().unique()):
                project_df = working_df[working_df["project"] == project_name].copy()
                safe_sheet_name = make_safe_sheet_name(project_name, used_names)
                project_df.to_excel(writer, index=False, sheet_name=safe_sheet_name)

    output.seek(0)
    return output.getvalue()


def render_metrics(tasks_df: pd.DataFrame) -> None:
    total = len(tasks_df)
    done = len(tasks_df[tasks_df["status"] == "Done"]) if not tasks_df.empty else 0
    blocked = len(tasks_df[tasks_df["status"] == "Blocked"]) if not tasks_df.empty else 0
    due_soon = 0

    if not tasks_df.empty and "due_date" in tasks_df.columns:
        temp = tasks_df.copy()
        temp["due_date"] = pd.to_datetime(temp["due_date"], errors="coerce")
        today = pd.Timestamp(date.today())
        due_soon = len(
            temp[
                (temp["due_date"].notna())
                & (temp["due_date"] >= today)
                & (temp["due_date"] <= today + pd.Timedelta(days=7))
            ]
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Tasks", total)
    c2.metric("Done", done)
    c3.metric("Blocked", blocked)
    c4.metric("Due in 7 Days", due_soon)


def main() -> None:
    st.set_page_config(page_title="Internal Project Tracker", layout="wide")
    st.title("Internal Project Tracker")
    st.caption("Shared internal tracker using Streamlit + Supabase.")

    supabase = get_supabase()

    users_df = fetch_users(supabase)
    projects_df = fetch_projects(supabase)
    tasks_df = fetch_tasks(supabase)

    st.sidebar.header("Filters")
    status_filter = st.sidebar.selectbox("Status", ["All"] + STATUSES)
    owner_names = users_df["name"].tolist() if not users_df.empty else []
    project_names = projects_df["name"].tolist() if not projects_df.empty else []
    owner_filter = st.sidebar.selectbox("Owner", ["All"] + owner_names)
    project_filter = st.sidebar.selectbox("Project", ["All"] + project_names)

    filtered_df = tasks_df.copy()
    if not filtered_df.empty:
        if status_filter != "All":
            filtered_df = filtered_df[filtered_df["status"] == status_filter]
        if owner_filter != "All":
            filtered_df = filtered_df[
                (filtered_df["owner_primary"] == owner_filter)
                | (filtered_df["owner_secondary"] == owner_filter)
            ]
        if project_filter != "All":
            filtered_df = filtered_df[filtered_df["project"] == project_filter]

    render_metrics(filtered_df)
    st.divider()

    left, right = st.columns([1.2, 1])

    with left:
        st.subheader("Tasks")
        if filtered_df.empty:
            st.info("No tasks found.")
        else:
            display_df = filtered_df[
                [
                    "id",
                    "title",
                    "project",
                    "owner_primary",
                    "owner_secondary",
                    "progress_percent",
                    "due_date",
                    "status",
                    "latest_update",
                    "notes",
                    "updated_at",
                ]
            ]
            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "progress_percent": st.column_config.ProgressColumn(
                        "Progress",
                        min_value=0,
                        max_value=100,
                        format="%d%%",
                    )
                },
            )

            excel_bytes = dataframe_to_excel_bytes(display_df)
            st.download_button(
                label="Export to Excel",
                data=excel_bytes,
                file_name="project_tracker_projects_export.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    with right:
        with st.expander("Add User"):
            with st.form("add_user_form", clear_on_submit=True):
                user_name = st.text_input("User name")
                if st.form_submit_button("Add user") and user_name.strip():
                    add_user(supabase, user_name.strip())
                    st.success("User added.")
                    st.rerun()

        with st.expander("Add Project"):
            with st.form("add_project_form", clear_on_submit=True):
                project_name = st.text_input("Project name")
                project_description = st.text_area("Description")
                project_due_date = st.date_input("Project due date", value=None)
                project_status = st.selectbox("Project status", PROJECT_STATUSES)
                if st.form_submit_button("Create project") and project_name.strip():
                    add_project(
                        supabase,
                        project_name.strip(),
                        project_description.strip(),
                        project_due_date.isoformat() if project_due_date else None,
                        project_status,
                    )
                    st.success("Project created.")
                    st.rerun()

        with st.expander("Edit or Delete Project"):
            if projects_df.empty:
                st.info("No projects available.")
            else:
                project_options = {f"#{row['id']} - {row['name']}": row for _, row in projects_df.iterrows()}
                selected_project_label = st.selectbox("Select project", list(project_options.keys()))
                selected_project_row = project_options[selected_project_label]
                existing_project_due = pd.to_datetime(selected_project_row["due_date"], errors="coerce")

                with st.form("edit_project_form"):
                    edit_project_name = st.text_input("Project name", value=selected_project_row["name"] or "")
                    edit_project_description = st.text_area("Description", value=selected_project_row["description"] or "")
                    edit_project_due = st.date_input(
                        "Project due date",
                        value=existing_project_due.date() if pd.notna(existing_project_due) else None,
                    )
                    edit_project_status = st.selectbox(
                        "Project status",
                        PROJECT_STATUSES,
                        index=PROJECT_STATUSES.index(selected_project_row["status"]) if selected_project_row["status"] in PROJECT_STATUSES else 0,
                    )
                    confirm_delete_project = st.checkbox("I confirm I want to permanently delete this project")

                    save_project = st.form_submit_button("Save project changes")
                    delete_project_btn = st.form_submit_button("Delete project")

                    if save_project and edit_project_name.strip():
                        update_project(
                            supabase,
                            int(selected_project_row["id"]),
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
                            delete_project(supabase, int(selected_project_row["id"]))
                            st.success("Project deleted.")
                            st.rerun()

        with st.expander("Add Task"):
            with st.form("add_task_form", clear_on_submit=True):
                title = st.text_input("Task title")
                project_map = {row["name"]: row["id"] for _, row in projects_df.iterrows()} if not projects_df.empty else {}
                user_map = {row["name"]: row["id"] for _, row in users_df.iterrows()} if not users_df.empty else {}

                selected_project = st.selectbox("Project", [None] + list(project_map.keys()), format_func=lambda x: x or "None")
                selected_primary = st.selectbox("Primary owner", list(user_map.keys()) if user_map else [None])
                selected_secondary = st.selectbox("Secondary owner", [None] + list(user_map.keys()), format_func=lambda x: x or "None")
                progress_percent = st.slider("Progress %", 0, 100, 0)
                due_date = st.date_input("Due date", value=None)
                status = st.selectbox("Status", STATUSES)
                latest_update = st.text_area("Latest progress update")
                notes = st.text_area("Notes")

                if st.form_submit_button("Create task") and title.strip() and selected_primary:
                    add_task(
                        supabase,
                        title.strip(),
                        project_map.get(selected_project),
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

    st.divider()
    st.subheader("Edit or Delete Task")
    if filtered_df.empty:
        st.info("No tasks available to edit.")
    else:
        task_options = {f"#{row['id']} - {row['title']}": row for _, row in filtered_df.iterrows()}
        selected_label = st.selectbox("Select task", list(task_options.keys()))
        selected_task = task_options[selected_label]

        with st.form("edit_task_form"):
            task_project_map = {row["name"]: row["id"] for _, row in projects_df.iterrows()} if not projects_df.empty else {}
            task_user_map = {row["name"]: row["id"] for _, row in users_df.iterrows()} if not users_df.empty else {}

            current_project_name = None
            for name, pid in task_project_map.items():
                if pid == selected_task["project_id"]:
                    current_project_name = name
                    break

            current_primary_name = None
            for name, uid in task_user_map.items():
                if uid == selected_task["owner_primary_id"]:
                    current_primary_name = name
                    break

            current_secondary_name = None
            for name, uid in task_user_map.items():
                if uid == selected_task["owner_secondary_id"]:
                    current_secondary_name = name
                    break

            edit_title = st.text_input("Task title", value=selected_task["title"] or "")
            edit_project_name = st.selectbox(
                "Project",
                [None] + list(task_project_map.keys()),
                index=([None] + list(task_project_map.keys())).index(current_project_name) if current_project_name in ([None] + list(task_project_map.keys())) else 0,
                format_func=lambda x: x or "None",
            )
            edit_primary_owner = st.selectbox(
                "Primary owner",
                list(task_user_map.keys()) if task_user_map else [None],
                index=(list(task_user_map.keys()).index(current_primary_name) if current_primary_name in list(task_user_map.keys()) else 0) if task_user_map else 0,
            )
            edit_secondary_owner = st.selectbox(
                "Secondary owner",
                [None] + list(task_user_map.keys()),
                index=([None] + list(task_user_map.keys())).index(current_secondary_name) if current_secondary_name in ([None] + list(task_user_map.keys())) else 0,
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
            confirm_delete_task = st.checkbox("I confirm I want to permanently delete this task")

            save_task = st.form_submit_button("Save task changes")
            delete_task_btn = st.form_submit_button("Delete task")

            if save_task and edit_title.strip() and edit_primary_owner:
                supabase.table("tasks").update(
                    {
                        "title": edit_title.strip(),
                        "project_id": task_project_map.get(edit_project_name),
                        "owner_primary_id": task_user_map.get(edit_primary_owner),
                        "owner_secondary_id": task_user_map.get(edit_secondary_owner) if edit_secondary_owner else None,
                        "progress_percent": edit_progress,
                        "due_date": edit_due.isoformat() if edit_due else None,
                        "status": edit_status,
                        "latest_update": edit_latest_update.strip(),
                        "notes": edit_notes.strip(),
                    }
                ).eq("id", int(selected_task["id"])).execute()
                st.success("Task updated.")
                st.rerun()

            if delete_task_btn:
                if not confirm_delete_task:
                    st.error("Please check the confirmation box before deleting this task.")
                else:
                    delete_task(supabase, int(selected_task["id"]))
                    st.success("Task deleted.")
                    st.rerun()


if __name__ == "__main__":
    main()


# requirements.txt
# streamlit
# pandas
# supabase
# openpyxl


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
# Optional starter data:
# insert into public.users (name) values ('Alice'), ('Bob'), ('Carol') on conflict do nothing;
# insert into public.projects (name, description, status) values
# ('Website Refresh', 'Internal website updates', 'In Progress'),
# ('Q2 Campaign', 'Cross-team campaign planning', 'Not Started')
# on conflict do nothing;
