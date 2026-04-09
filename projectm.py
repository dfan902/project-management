# streamlit_project_tracker_starter.py
# Version 1 starter for a lightweight internal project tracker
# Run with: streamlit run streamlit_project_tracker_starter.py

import sqlite3
from contextlib import closing
from datetime import date
import streamlit as st
import pandas as pd

DB_PATH = "project_tracker.db"
STATUSES = ["Not Started", "In Progress", "Blocked", "Done"]


# -------------------------
# Database helpers
# -------------------------

def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    with closing(get_connection()) as conn:
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                due_date TEXT,
                status TEXT NOT NULL DEFAULT 'In Progress'
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                project_id INTEGER,
                owner_primary_id INTEGER,
                owner_secondary_id INTEGER,
                progress_percent INTEGER NOT NULL DEFAULT 0,
                due_date TEXT,
                status TEXT NOT NULL DEFAULT 'Not Started',
                latest_update TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id),
                FOREIGN KEY (owner_primary_id) REFERENCES users(id),
                FOREIGN KEY (owner_secondary_id) REFERENCES users(id)
            )
            """
        )

        conn.commit()


def seed_users_if_empty(names=None):
    if names is None:
        names = ["Alice", "Bob", "Carol"]

    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        count = cur.fetchone()[0]

        if count == 0:
            for name in names:
                cur.execute("INSERT INTO users (name) VALUES (?)", (name,))
            conn.commit()


# -------------------------
# Query helpers
# -------------------------

def fetch_users():
    with closing(get_connection()) as conn:
        return pd.read_sql_query("SELECT id, name FROM users ORDER BY name", conn)


def fetch_projects():
    with closing(get_connection()) as conn:
        return pd.read_sql_query(
            "SELECT id, name, description, due_date, status FROM projects ORDER BY name", conn
        )


def fetch_tasks(status_filter=None, owner_filter=None, project_filter=None):
    query = """
        SELECT
            t.id,
            t.title,
            p.name AS project,
            u1.name AS owner_primary,
            u2.name AS owner_secondary,
            t.progress_percent,
            t.due_date,
            t.status,
            t.latest_update,
            t.notes,
            t.updated_at
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        LEFT JOIN users u1 ON t.owner_primary_id = u1.id
        LEFT JOIN users u2 ON t.owner_secondary_id = u2.id
        WHERE 1=1
    """
    params = []

    if status_filter and status_filter != "All":
        query += " AND t.status = ?"
        params.append(status_filter)

    if owner_filter and owner_filter != "All":
        query += " AND (u1.name = ? OR u2.name = ?)"
        params.extend([owner_filter, owner_filter])

    if project_filter and project_filter != "All":
        query += " AND p.name = ?"
        params.append(project_filter)

    query += " ORDER BY COALESCE(t.due_date, '9999-12-31'), t.updated_at DESC"

    with closing(get_connection()) as conn:
        return pd.read_sql_query(query, conn, params=params)


# -------------------------
# Insert / update helpers
# -------------------------

def add_project(name, description, due_date, status):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO projects (name, description, due_date, status) VALUES (?, ?, ?, ?)",
            (name, description, due_date, status),
        )
        conn.commit()


def add_user(name):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO users (name) VALUES (?)", (name,))
        conn.commit()


def add_task(title, project_id, owner_primary_id, owner_secondary_id, progress_percent, due_date, status, latest_update, notes):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tasks (
                title, project_id, owner_primary_id, owner_secondary_id,
                progress_percent, due_date, status, latest_update, notes, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                title,
                project_id,
                owner_primary_id,
                owner_secondary_id,
                progress_percent,
                due_date,
                status,
                latest_update,
                notes,
            ),
        )
        conn.commit()


def update_task(task_id, progress_percent, due_date, status, latest_update, notes):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tasks
            SET progress_percent = ?,
                due_date = ?,
                status = ?,
                latest_update = ?,
                notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (progress_percent, due_date, status, latest_update, notes, task_id),
        )
        conn.commit()


# -------------------------
# UI
# -------------------------

def render_sidebar():
    st.sidebar.header("Filters")

    users_df = fetch_users()
    projects_df = fetch_projects()

    status_filter = st.sidebar.selectbox("Status", ["All"] + STATUSES)
    owner_filter = st.sidebar.selectbox("Owner", ["All"] + users_df["name"].tolist())
    project_filter = st.sidebar.selectbox("Project", ["All"] + projects_df["name"].tolist())

    return status_filter, owner_filter, project_filter


def render_metrics(tasks_df):
    total = len(tasks_df)
    done = len(tasks_df[tasks_df["status"] == "Done"])
    blocked = len(tasks_df[tasks_df["status"] == "Blocked"])
    due_soon = 0

    if not tasks_df.empty and "due_date" in tasks_df.columns:
        temp = tasks_df.copy()
        temp["due_date"] = pd.to_datetime(temp["due_date"], errors="coerce")
        today = pd.Timestamp(date.today())
        due_soon = len(temp[(temp["due_date"].notna()) & (temp["due_date"] >= today) & (temp["due_date"] <= today + pd.Timedelta(days=7))])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Tasks", total)
    c2.metric("Done", done)
    c3.metric("Blocked", blocked)
    c4.metric("Due in 7 Days", due_soon)


def render_tasks_table(tasks_df):
    st.subheader("Tasks")
    if tasks_df.empty:
        st.info("No tasks found.")
        return

    st.dataframe(
        tasks_df,
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


def render_add_project():
    with st.expander("Add Project"):
        with st.form("add_project_form", clear_on_submit=True):
            name = st.text_input("Project name")
            description = st.text_area("Description")
            due_date = st.date_input("Project due date", value=None)
            status = st.selectbox("Project status", ["Not Started", "In Progress", "Done"])
            submitted = st.form_submit_button("Create project")

            if submitted and name.strip():
                add_project(
                    name.strip(),
                    description.strip(),
                    due_date.isoformat() if due_date else None,
                    status,
                )
                st.success("Project created.")
                st.rerun()


def render_add_user():
    with st.expander("Add User"):
        with st.form("add_user_form", clear_on_submit=True):
            name = st.text_input("User name")
            submitted = st.form_submit_button("Add user")
            if submitted and name.strip():
                add_user(name.strip())
                st.success("User added.")
                st.rerun()


def render_add_task():
    users_df = fetch_users()
    projects_df = fetch_projects()

    with st.expander("Add Task"):
        with st.form("add_task_form", clear_on_submit=True):
            title = st.text_input("Task title")

            project_map = {row["name"]: row["id"] for _, row in projects_df.iterrows()}
            user_map = {row["name"]: row["id"] for _, row in users_df.iterrows()}

            project_name = st.selectbox("Project", [None] + list(project_map.keys()), format_func=lambda x: x or "None")
            owner_primary_name = st.selectbox("Primary owner", list(user_map.keys()))
            owner_secondary_name = st.selectbox("Secondary owner", [None] + list(user_map.keys()), format_func=lambda x: x or "None")
            progress_percent = st.slider("Progress %", 0, 100, 0)
            due_date = st.date_input("Due date", value=None)
            status = st.selectbox("Status", STATUSES)
            latest_update = st.text_area("Latest progress update")
            notes = st.text_area("Notes")

            submitted = st.form_submit_button("Create task")

            if submitted and title.strip():
                add_task(
                    title=title.strip(),
                    project_id=project_map.get(project_name),
                    owner_primary_id=user_map.get(owner_primary_name),
                    owner_secondary_id=user_map.get(owner_secondary_name) if owner_secondary_name else None,
                    progress_percent=progress_percent,
                    due_date=due_date.isoformat() if due_date else None,
                    status=status,
                    latest_update=latest_update.strip(),
                    notes=notes.strip(),
                )
                st.success("Task created.")
                st.rerun()


def render_edit_task():
    st.subheader("Update Task")
    tasks_df = fetch_tasks()

    if tasks_df.empty:
        st.info("No tasks available to edit yet.")
        return

    task_options = {
        f"#{row['id']} - {row['title']}": row for _, row in tasks_df.iterrows()
    }
    selected_label = st.selectbox("Select task", list(task_options.keys()))
    selected = task_options[selected_label]

    with st.form("edit_task_form"):
        progress_percent = st.slider(
            "Progress %",
            0,
            100,
            int(selected["progress_percent"] or 0),
        )

        existing_due = pd.to_datetime(selected["due_date"], errors="coerce")
        due_date = st.date_input(
            "Due date",
            value=existing_due.date() if pd.notna(existing_due) else None,
        )

        status = st.selectbox(
            "Status",
            STATUSES,
            index=STATUSES.index(selected["status"]) if selected["status"] in STATUSES else 0,
        )
        latest_update = st.text_area("Latest progress update", value=selected["latest_update"] or "")
        notes = st.text_area("Notes", value=selected["notes"] or "")

        submitted = st.form_submit_button("Save changes")
        if submitted:
            update_task(
                task_id=int(selected["id"]),
                progress_percent=progress_percent,
                due_date=due_date.isoformat() if due_date else None,
                status=status,
                latest_update=latest_update.strip(),
                notes=notes.strip(),
            )
            st.success("Task updated.")
            st.rerun()


def main():
    st.set_page_config(page_title="Internal Project Tracker", layout="wide")
    st.title("Internal Project Tracker")
    st.caption("A lightweight version 1 for tracking tasks, owners, progress, due dates, and status.")

    init_db()
    seed_users_if_empty()

    status_filter, owner_filter, project_filter = render_sidebar()
    tasks_df = fetch_tasks(status_filter, owner_filter, project_filter)

    render_metrics(tasks_df)
    st.divider()

    left, right = st.columns([1.2, 1])

    with left:
        render_tasks_table(tasks_df)

    with right:
        render_add_project()
        render_add_user()
        render_add_task()

    st.divider()
    render_edit_task()


if __name__ == "__main__":
    main()
