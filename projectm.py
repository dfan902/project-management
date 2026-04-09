# app.py
# Streamlit + Supabase version of a lightweight internal project tracker
# Run locally with: streamlit run app.py

from datetime import date
import pandas as pd
import streamlit as st
from io import BytesIO
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

def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    export_df = df.copy()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        # Keep only non-completed projects for separate sheets
        working_df = export_df[export_df["project"].notna()].copy()
        if "status" in working_df.columns:
            working_df = working_df[working_df["status"] != "Done"]

        # Summary sheet
        export_df.to_excel(writer, index=False, sheet_name="All Visible Tasks")

        # One sheet per active project
        if not working_df.empty:
            for project_name in sorted(working_df["project"].dropna().unique()):
                project_df = working_df[working_df["project"] == project_name].copy()
                safe_sheet_name = str(project_name)[:31]
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
    st.subheader("Update Task")
    if filtered_df.empty:
        st.info("No tasks available to edit.")
    else:
        task_options = {f"#{row['id']} - {row['title']}": row for _, row in filtered_df.iterrows()}
        selected_label = st.selectbox("Select task", list(task_options.keys()))
        selected_task = task_options[selected_label]

        with st.form("edit_task_form"):
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

            if st.form_submit_button("Save changes"):
                update_task(
                    supabase,
                    int(selected_task["id"]),
                    edit_progress,
                    edit_due.isoformat() if edit_due else None,
                    edit_status,
                    edit_latest_update.strip(),
                    edit_notes.strip(),
                )
                st.success("Task updated.")
                st.rerun()


if __name__ == "__main__":
    main()


# requirements.txt
# streamlit
# pandas
# supabase
# openpyxl


