# app.py
# Streamlit + Supabase internal project tracker
# Full version with:
# - Home page project cards
# - Add project from Home
# - Project page timeline + updates
# - Project page task actions via top buttons
# - Real team field on tasks
# - Settings for users, teams, and export

from datetime import date, timedelta
from io import BytesIO
import re

import pandas as pd
import plotly.express as px
import streamlit as st
from supabase import Client, create_client

STATUSES = ["Not Started", "In Progress", "Blocked", "Done"]
PROJECT_STATUSES = ["Not Started", "In Progress", "Done"]
TASK_COLUMNS = [
    "id",
    "title",
    "team",
    "project",
    "owner_primary",
    "owner_secondary",
    "progress_percent",
    "start_date",
    "due_date",
    "status",
    "latest_update",
    "notes",
    "updated_at",
    "project_id",
    "owner_primary_id",
    "owner_secondary_id",
]


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


def fetch_teams(supabase: Client) -> pd.DataFrame:
    try:
        response = supabase.table("teams").select("id, name").order("name").execute()
    except Exception:
        df = pd.DataFrame(columns=["id", "name"])
        df.attrs["missing_table"] = True
        return df

    df = pd.DataFrame(response.data or [], columns=["id", "name"])
    df.attrs["missing_table"] = False
    return df


def fetch_projects(supabase: Client) -> pd.DataFrame:
    response = (
        supabase.table("projects")
        .select("id, name, description, due_date, status")
        .order("name")
        .execute()
    )
    return pd.DataFrame(response.data or [])


def fetch_tasks(supabase: Client) -> pd.DataFrame:
    task_select = """
        id,
        title,
        team,
        progress_percent,
        start_date,
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
    try:
        response = supabase.table("tasks").select(task_select).order("due_date", desc=False).execute()
    except Exception:
        fallback_select = task_select.replace("        start_date,\n", "")
        response = supabase.table("tasks").select(fallback_select).order("due_date", desc=False).execute()

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
                "start_date": item.get("start_date"),
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

    return pd.DataFrame(rows, columns=TASK_COLUMNS)


def add_user(supabase: Client, name: str) -> None:
    supabase.table("users").insert({"name": name}).execute()


def add_team(supabase: Client, name: str) -> None:
    supabase.table("teams").insert({"name": name}).execute()


def update_team(supabase: Client, team_id: int, old_name: str, new_name: str) -> None:
    supabase.table("teams").update({"name": new_name}).eq("id", team_id).execute()
    if old_name != new_name:
        supabase.table("tasks").update({"team": new_name}).eq("team", old_name).execute()


def delete_team(supabase: Client, team_id: int, team_name: str) -> None:
    supabase.table("tasks").update({"team": None}).eq("team", team_name).execute()
    supabase.table("teams").delete().eq("id", team_id).execute()


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
    team: str | None,
    project_id: int | None,
    owner_primary_id: int | None,
    owner_secondary_id: int | None,
    progress_percent: int,
    start_date: str | None,
    due_date: str | None,
    status: str,
    latest_update: str,
    notes: str,
) -> None:
    try:
        supabase.table("tasks").insert(
            {
                "title": title,
                "team": team,
                "project_id": project_id,
                "owner_primary_id": owner_primary_id,
                "owner_secondary_id": owner_secondary_id,
                "progress_percent": progress_percent,
                "start_date": start_date,
                "due_date": due_date,
                "status": status,
                "latest_update": latest_update,
                "notes": notes,
            }
        ).execute()
    except Exception as exc:
        raise RuntimeError("Task start dates require the `start_date` column in Supabase.") from exc


def update_task_full(
    supabase: Client,
    task_id: int,
    title: str,
    team: str | None,
    project_id: int | None,
    owner_primary_id: int | None,
    owner_secondary_id: int | None,
    progress_percent: int,
    start_date: str | None,
    due_date: str | None,
    status: str,
    latest_update: str,
    notes: str,
) -> None:
    try:
        supabase.table("tasks").update(
            {
                "title": title,
                "team": team,
                "project_id": project_id,
                "owner_primary_id": owner_primary_id,
                "owner_secondary_id": owner_secondary_id,
                "progress_percent": progress_percent,
                "start_date": start_date,
                "due_date": due_date,
                "status": status,
                "latest_update": latest_update,
                "notes": notes,
                "updated_at": pd.Timestamp.utcnow().isoformat(),
            }
        ).eq("id", task_id).execute()
    except Exception as exc:
        raise RuntimeError("Task start dates require the `start_date` column in Supabase.") from exc


def update_task_quick(
    supabase: Client,
    task_id: int,
    status: str,
    progress_percent: int,
    latest_update: str,
) -> None:
    supabase.table("tasks").update(
        {
            "status": status,
            "progress_percent": progress_percent,
            "latest_update": latest_update,
            "updated_at": pd.Timestamp.utcnow().isoformat(),
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
    if "start_date" not in df.columns:
        df["start_date"] = pd.NaT
    df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce")
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

    inferred_start = df.apply(infer_start, axis=1)
    df["start_date"] = df["start_date"].fillna(inferred_start)
    df["task_label"] = df["title"]
    today_date = pd.Timestamp(date.today())
    df["days_remaining"] = (df["due_date"] - today_date).dt.days
    df["urgency"] = df.apply(lambda row: due_label(row.get("due_date"), row.get("status")), axis=1)
    df["is_overdue"] = (df["due_date"] < today_date) & (df["status"] != "Done")
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
            "days_remaining": True,
            "status": True,
            "due_date": True,
            "urgency": True,
            "task_label": False,
        },
    )

    fig.update_traces(opacity=0.72)
    overdue_labels = chart_df.loc[chart_df["is_overdue"], "task_label"].tolist()
    if overdue_labels:
        fig.for_each_trace(lambda trace: trace.update(opacity=1.0) if trace.name in overdue_labels else None)

    today = pd.Timestamp(date.today())
    fig.add_vline(
        x=today,
        line_width=2,
        line_dash="dash",
        line_color="#111827",
        annotation_text="Today",
        annotation_position="top",
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

def normalize_team_value(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def status_badge(status: str | None) -> str:
    colors = {
        "Not Started": ("#f3f4f6", "#374151"),
        "In Progress": ("#dbeafe", "#1d4ed8"),
        "Blocked": ("#fee2e2", "#b91c1c"),
        "Done": ("#dcfce7", "#166534"),
    }
    bg, fg = colors.get(status or "", ("#f3f4f6", "#374151"))
    return (
        f"<span style='background:{bg};color:{fg};padding:0.18rem 0.5rem;"
        f"border-radius:999px;font-size:0.78rem;font-weight:700;'>{status or 'Unknown'}</span>"
    )


def project_status_badge(status: str | None) -> str:
    return status_badge(status)


def due_label(due_date_value, status: str | None = None) -> str:
    due = pd.to_datetime(due_date_value, errors="coerce")
    if pd.isna(due):
        return "No due date"
    if status == "Done":
        return f"Done, due {due.date().isoformat()}"

    days = (due.date() - date.today()).days
    if days == 0:
        return "Due today"
    if days < 0:
        return f"Overdue by {abs(days)} day{'s' if abs(days) != 1 else ''}"
    return f"Due in {days} day{'s' if days != 1 else ''}"


def relative_time(value) -> str:
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(timestamp):
        return "No recent timestamp"

    now = pd.Timestamp.utcnow()
    delta = now - timestamp
    if delta.days >= 1:
        return f"{delta.days} day{'s' if delta.days != 1 else ''} ago"

    hours = int(delta.total_seconds() // 3600)
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"

    minutes = max(1, int(delta.total_seconds() // 60))
    return f"{minutes} minute{'s' if minutes != 1 else ''} ago"


def task_is_open(row) -> bool:
    return row.get("status") != "Done"


def task_is_overdue(row) -> bool:
    due = pd.to_datetime(row.get("due_date"), errors="coerce")
    return pd.notna(due) and due.date() < date.today() and row.get("status") != "Done"


def task_is_due_today(row) -> bool:
    due = pd.to_datetime(row.get("due_date"), errors="coerce")
    return pd.notna(due) and due.date() == date.today() and row.get("status") != "Done"


def project_progress(project_tasks: pd.DataFrame) -> int:
    if project_tasks.empty:
        return 0
    progress = pd.to_numeric(project_tasks["progress_percent"], errors="coerce").fillna(0)
    return int(round(progress.mean()))


def smart_sort_tasks(tasks_df: pd.DataFrame) -> pd.DataFrame:
    if tasks_df.empty:
        return tasks_df

    df = tasks_df.copy()
    due_dates = pd.to_datetime(df["due_date"], errors="coerce")
    today = pd.Timestamp(date.today())
    status_rank = {
        "Blocked": 2,
        "In Progress": 3,
        "Not Started": 4,
        "Done": 5,
    }
    df["_sort_group"] = 6
    df.loc[(due_dates < today) & (df["status"] != "Done"), "_sort_group"] = 0
    df.loc[(due_dates == today) & (df["status"] != "Done"), "_sort_group"] = 1
    for status, rank in status_rank.items():
        df.loc[(df["_sort_group"] == 6) & (df["status"] == status), "_sort_group"] = rank
    df["_sort_due"] = due_dates.fillna(pd.Timestamp.max)
    df = df.sort_values(["_sort_group", "_sort_due", "title"], na_position="last")
    return df.drop(columns=["_sort_group", "_sort_due"])


def filter_tasks_by_attention(tasks_df: pd.DataFrame, attention_filter: str | None) -> pd.DataFrame:
    if tasks_df.empty or not attention_filter:
        return tasks_df

    df = tasks_df.copy()
    if attention_filter == "overdue":
        return df[df.apply(task_is_overdue, axis=1)]
    if attention_filter == "due_today":
        return df[df.apply(task_is_due_today, axis=1)]
    if attention_filter == "blocked":
        return df[df["status"] == "Blocked"]
    return df


def render_metric_card(label: str, value: int, helper: str, key: str, attention_filter: str) -> None:
    with st.container(border=True):
        st.metric(label, value)
        st.caption(helper)
        if st.button("Show tasks", key=key, use_container_width=True):
            st.session_state["home_attention_filter"] = attention_filter
            st.rerun()


def render_task_table(tasks_df: pd.DataFrame) -> None:
    if tasks_df.empty:
        st.info("No matching tasks.")
        return

    display_df = tasks_df[
        [
            "project",
            "team",
            "title",
            "owner_primary",
            "owner_secondary",
            "progress_percent",
            "start_date",
            "due_date",
            "status",
            "latest_update",
            "notes",
        ]
    ].copy()
    display_df["urgency"] = display_df.apply(lambda row: due_label(row["due_date"], row["status"]), axis=1)
    display_df = display_df.rename(
        columns={
            "project": "Project",
            "team": "Team",
            "title": "Task",
            "owner_primary": "Primary Owner",
            "owner_secondary": "Secondary Owner",
            "progress_percent": "Progress %",
            "start_date": "Start Date",
            "due_date": "Due Date",
            "status": "Status",
            "latest_update": "Latest Update",
            "notes": "Notes",
            "urgency": "Urgency",
        }
    )
    st.dataframe(display_df, use_container_width=True, hide_index=True)


def task_payload_from_form(
    selected_team: str | None,
    selected_primary: str | None,
    selected_secondary: str | None,
    user_map: dict[str, int],
) -> tuple[str | None, int | None, int | None]:
    team = normalize_team_value(selected_team)
    primary_owner_id = user_map.get(selected_primary) if selected_primary else None
    secondary_owner_id = user_map.get(selected_secondary) if selected_secondary else None
    return team, primary_owner_id, secondary_owner_id


def render_top_bar() -> None:
    top_left, top_right = st.columns([6, 1])
    with top_left:
        st.title("Project Tracker")
        st.caption("Internal project visibility dashboard")
    with top_right:
        if st.button("⚙️", help="Open settings", use_container_width=True):
            st.session_state["page"] = "Settings"
            st.rerun()


def render_project_edit_dialog(supabase: Client, project_row: pd.Series) -> None:
    existing_project_due = pd.to_datetime(project_row["due_date"], errors="coerce")

    with st.form("project_edit_dialog_form"):
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
        st.divider()
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
            st.session_state["active_dialog"] = None
            st.success("Project updated.")
            st.rerun()

        if delete_project_btn:
            if not confirm_delete_project:
                st.error("Please check the confirmation box before deleting this project.")
            else:
                delete_project(supabase, int(project_row["id"]))
                st.session_state["page"] = "Home"
                st.session_state["selected_project_id"] = None
                st.session_state["selected_task_id"] = None
                st.session_state["active_dialog"] = None
                st.success("Project deleted.")
                st.rerun()

    if st.button("Cancel", key="cancel_project_edit_dialog"):
        st.session_state["active_dialog"] = None
        st.rerun()


def render_add_task_dialog(
    supabase: Client,
    project_id: int,
    users_df: pd.DataFrame,
    teams_df: pd.DataFrame,
) -> None:
    user_map = {row["name"]: row["id"] for _, row in users_df.iterrows()} if not users_df.empty else {}
    teams_table_missing = teams_df.attrs.get("missing_table", False)
    team_names = sorted(teams_df["name"].dropna().tolist()) if "name" in teams_df.columns else []

    with st.form("add_task_dialog_form", clear_on_submit=True):
        title = st.text_input("Task title")
        if teams_table_missing:
            team = st.text_input("Team", help="Create a `teams` table to manage team values from Settings.")
        else:
            team = st.selectbox("Team", [""] + team_names, format_func=lambda x: x or "None")
        selected_primary = st.selectbox("Primary owner", list(user_map.keys()) if user_map else [None])
        selected_secondary = st.selectbox(
            "Secondary owner",
            [None] + list(user_map.keys()),
            format_func=lambda x: x or "None",
        )
        progress_percent = st.slider("Progress %", 0, 100, 0)
        start_date = st.date_input("Start date", value=None)
        due_date = st.date_input("Due date", value=None)
        status = st.selectbox("Status", STATUSES)
        latest_update = st.text_area("Latest progress update")
        notes = st.text_area("Notes")

        submitted = st.form_submit_button("Create task")
        if submitted and title.strip() and selected_primary:
            team_value, primary_owner_id, secondary_owner_id = task_payload_from_form(
                team,
                selected_primary,
                selected_secondary,
                user_map,
            )
            try:
                add_task(
                    supabase,
                    title.strip(),
                    team_value,
                    project_id,
                    primary_owner_id,
                    secondary_owner_id,
                    progress_percent,
                    start_date.isoformat() if start_date else None,
                    due_date.isoformat() if due_date else None,
                    status,
                    latest_update.strip(),
                    notes.strip(),
                )
            except RuntimeError as exc:
                st.error(f"{exc} Run: `alter table public.tasks add column if not exists start_date date;`")
            else:
                st.session_state["active_dialog"] = None
                st.success("Task created.")
                st.rerun()

    if st.button("Cancel", key="cancel_add_task_dialog"):
        st.session_state["active_dialog"] = None
        st.rerun()


def render_task_edit_dialog(
    supabase: Client,
    task_row: pd.Series,
    project_id: int,
    users_df: pd.DataFrame,
    teams_df: pd.DataFrame,
) -> None:
    user_map = {row["name"]: row["id"] for _, row in users_df.iterrows()} if not users_df.empty else {}
    teams_table_missing = teams_df.attrs.get("missing_table", False)
    team_names = sorted(teams_df["name"].dropna().tolist()) if "name" in teams_df.columns else []
    current_primary_name = next((name for name, uid in user_map.items() if uid == task_row["owner_primary_id"]), None)
    current_secondary_name = next((name for name, uid in user_map.items() if uid == task_row["owner_secondary_id"]), None)
    current_team_name = task_row["team"] or ""
    existing_start = pd.to_datetime(task_row["start_date"], errors="coerce")
    existing_due = pd.to_datetime(task_row["due_date"], errors="coerce")

    with st.form(f"edit_task_dialog_form_{task_row['id']}"):
        edit_title = st.text_input("Task title", value=task_row["title"] or "")
        if teams_table_missing:
            edit_team = st.text_input(
                "Team",
                value=current_team_name,
                help="Create a `teams` table to manage team values from Settings.",
            )
        else:
            team_choices = [""] + sorted(set(team_names + ([current_team_name] if current_team_name else [])))
            edit_team = st.selectbox(
                "Team",
                team_choices,
                index=team_choices.index(current_team_name) if current_team_name in team_choices else 0,
                format_func=lambda x: x or "None",
            )
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
        edit_start = st.date_input("Start date", value=existing_start.date() if pd.notna(existing_start) else None)
        edit_due = st.date_input("Due date", value=existing_due.date() if pd.notna(existing_due) else None)
        edit_status = st.selectbox(
            "Status",
            STATUSES,
            index=STATUSES.index(task_row["status"]) if task_row["status"] in STATUSES else 0,
        )
        edit_progress = st.slider("Progress %", 0, 100, int(task_row["progress_percent"] or 0))
        edit_latest_update = st.text_area("Latest progress update", value=task_row["latest_update"] or "")
        edit_notes = st.text_area("Notes", value=task_row["notes"] or "")
        confirm_delete_task = st.checkbox("I confirm I want to permanently delete this task")

        save_task = st.form_submit_button("Save task changes")
        delete_task_btn = st.form_submit_button("Delete task")

        if save_task and edit_title.strip() and edit_primary_owner:
            team_value, primary_owner_id, secondary_owner_id = task_payload_from_form(
                edit_team,
                edit_primary_owner,
                edit_secondary_owner,
                user_map,
            )
            try:
                update_task_full(
                    supabase,
                    int(task_row["id"]),
                    edit_title.strip(),
                    team_value,
                    project_id,
                    primary_owner_id,
                    secondary_owner_id,
                    edit_progress,
                    edit_start.isoformat() if edit_start else None,
                    edit_due.isoformat() if edit_due else None,
                    edit_status,
                    edit_latest_update.strip(),
                    edit_notes.strip(),
                )
            except RuntimeError as exc:
                st.error(f"{exc} Run: `alter table public.tasks add column if not exists start_date date;`")
            else:
                st.session_state["active_dialog"] = None
                st.success("Task updated.")
                st.rerun()

        if delete_task_btn:
            if not confirm_delete_task:
                st.error("Please check the confirmation box before deleting this task.")
            else:
                delete_task(supabase, int(task_row["id"]))
                st.session_state["selected_task_id"] = None
                st.session_state["active_dialog"] = None
                st.success("Task deleted.")
                st.rerun()

    if st.button("Cancel", key=f"cancel_edit_task_dialog_{task_row['id']}"):
        st.session_state["active_dialog"] = None
        st.rerun()


def render_workload_summary(tasks_df: pd.DataFrame) -> None:
    open_tasks = tasks_df[tasks_df["status"] != "Done"].copy() if not tasks_df.empty else pd.DataFrame(columns=TASK_COLUMNS)
    if open_tasks.empty:
        st.info("No open tasks assigned yet.")
        return

    grouped = []
    for owner, owner_tasks in open_tasks.groupby(open_tasks["owner_primary"].fillna("Unassigned")):
        open_count = len(owner_tasks)
        overdue_count = int(owner_tasks.apply(task_is_overdue, axis=1).sum())
        grouped.append({"owner": owner, "open": open_count, "overdue": overdue_count})

    grouped = sorted(grouped, key=lambda row: (-row["overdue"], -row["open"], row["owner"]))
    cols = st.columns(min(4, max(1, len(grouped))))
    for index, row in enumerate(grouped):
        with cols[index % len(cols)]:
            with st.container(border=True):
                overloaded = row["open"] >= 6 or row["overdue"] >= 2
                st.markdown(f"**{row['owner']}**")
                st.metric("Open tasks", row["open"])
                st.caption(f"{row['overdue']} overdue")
                if overloaded:
                    st.warning("High load")


def render_home_recent_updates(tasks_df: pd.DataFrame, limit: int = 8) -> None:
    if tasks_df.empty:
        st.info("No recent task activity yet.")
        return

    recent_df = tasks_df.copy()
    recent_df["_updated"] = pd.to_datetime(recent_df["updated_at"], errors="coerce", utc=True)
    recent_df = recent_df.sort_values("_updated", ascending=False, na_position="last").head(limit)

    for _, task in recent_df.iterrows():
        with st.container(border=True):
            title_col, badge_col = st.columns([5, 1])
            with title_col:
                st.markdown(f"**{task['title']}**")
                st.caption(
                    f"{task.get('project') or 'No project'} | "
                    f"{task.get('owner_primary') or 'Unassigned'} | "
                    f"{relative_time(task.get('updated_at'))}"
                )
            with badge_col:
                st.markdown(status_badge(task.get("status")), unsafe_allow_html=True)
            st.write(task.get("latest_update") or "No update text yet.")


def render_home_v2(supabase: Client, projects_df: pd.DataFrame, tasks_df: pd.DataFrame) -> None:
    top_left, top_right = st.columns([5, 1])

    with top_left:
        st.subheader("Team command center")
        st.caption("Scan risk, workload, project health, and recent movement.")

    with top_right:
        with st.popover("Add project", use_container_width=True):
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

    view_mode = st.segmented_control(
        "View",
        ["All tasks", "My tasks", "Team view"],
        key="global_view_mode",
    )
    if view_mode != "All tasks":
        st.caption("Auth-aware filtering can use this control later. For now it keeps the dashboard in all-task mode.")

    st.markdown("#### Needs Attention")
    open_tasks = tasks_df[tasks_df["status"] != "Done"].copy() if not tasks_df.empty else pd.DataFrame(columns=TASK_COLUMNS)
    overdue_tasks = open_tasks[open_tasks.apply(task_is_overdue, axis=1)] if not open_tasks.empty else open_tasks
    due_today_tasks = open_tasks[open_tasks.apply(task_is_due_today, axis=1)] if not open_tasks.empty else open_tasks
    blocked_tasks = open_tasks[open_tasks["status"] == "Blocked"] if not open_tasks.empty else open_tasks

    attention_cols = st.columns(3)
    with attention_cols[0]:
        render_metric_card("Overdue", len(overdue_tasks), "Tasks past due and not done.", "home_show_overdue", "overdue")
    with attention_cols[1]:
        render_metric_card("Due today", len(due_today_tasks), "Tasks that need action today.", "home_show_due_today", "due_today")
    with attention_cols[2]:
        render_metric_card("Blocked", len(blocked_tasks), "Tasks waiting on help or decisions.", "home_show_blocked", "blocked")

    active_attention = st.session_state.get("home_attention_filter")
    if active_attention:
        filtered_attention = smart_sort_tasks(filter_tasks_by_attention(tasks_df, active_attention))
        st.markdown(f"#### Attention feed: {active_attention.replace('_', ' ').title()}")
        if st.button("Clear attention filter", key="clear_home_attention_filter"):
            st.session_state["home_attention_filter"] = None
            st.rerun()
        render_home_recent_updates(filtered_attention, limit=8)

    st.markdown("#### Workload by owner")
    render_workload_summary(tasks_df)

    st.markdown("#### Projects")
    if projects_df.empty:
        st.info("No projects available yet.")
    else:
        project_cards = []
        for _, project in projects_df.iterrows():
            project_name = project.get("name")
            project_tasks = tasks_df[tasks_df["project"] == project_name].copy() if not tasks_df.empty else pd.DataFrame(columns=TASK_COLUMNS)
            project_cards.append(
                {
                    "id": project.get("id"),
                    "name": project_name,
                    "status": project.get("status"),
                    "progress": project_progress(project_tasks),
                    "total_tasks": len(project_tasks),
                    "blocked_tasks": int((project_tasks["status"] == "Blocked").sum()) if not project_tasks.empty else 0,
                    "overdue_tasks": int(project_tasks.apply(task_is_overdue, axis=1).sum()) if not project_tasks.empty else 0,
                }
            )

        for start in range(0, len(project_cards), 3):
            cols = st.columns(3)
            for col, card in zip(cols, project_cards[start:start + 3]):
                with col:
                    with st.container(border=True):
                        st.markdown(f"### {card['name']}")
                        st.markdown(project_status_badge(card["status"]), unsafe_allow_html=True)
                        st.progress(card["progress"] / 100, text=f"{card['progress']}% complete")
                        metric_cols = st.columns(3)
                        metric_cols[0].metric("Tasks", card["total_tasks"])
                        metric_cols[1].metric("Overdue", card["overdue_tasks"])
                        metric_cols[2].metric("Blocked", card["blocked_tasks"])
                        if st.button("Open project", key=f"open_project_{card['id']}", use_container_width=True):
                            st.session_state["selected_project_id"] = int(card["id"])
                            st.session_state["selected_task_id"] = None
                            st.session_state["active_dialog"] = None
                            st.session_state["page"] = "Project"
                            st.rerun()

    st.markdown("#### Recent updates")
    render_home_recent_updates(tasks_df, limit=8)


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
            "start_date",
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
            "start_date": "Start Date",
            "due_date": "Due Date",
            "status": "Status",
            "latest_update": "Update",
            "notes": "Notes",
        }
    )

    st.dataframe(display_df, use_container_width=True, hide_index=True)


def render_quick_update_popover(supabase: Client, task: pd.Series) -> None:
    with st.popover("Quick update", use_container_width=True):
        with st.form(f"quick_update_form_{task['id']}"):
            quick_status = st.selectbox(
                "Status",
                STATUSES,
                index=STATUSES.index(task["status"]) if task["status"] in STATUSES else 0,
            )
            quick_progress = st.slider("Progress %", 0, 100, int(task["progress_percent"] or 0))
            quick_update = st.text_area("Short update", value=task["latest_update"] or "", height=90)
            submitted = st.form_submit_button("Save quick update")
            if submitted:
                update_task_quick(
                    supabase,
                    int(task["id"]),
                    quick_status,
                    quick_progress,
                    quick_update.strip(),
                )
                st.success("Task updated.")
                st.rerun()


def render_task_feed(
    supabase: Client,
    tasks_df: pd.DataFrame,
    project_id: int,
    users_df: pd.DataFrame,
    teams_df: pd.DataFrame,
) -> None:
    if tasks_df.empty:
        st.info("No matching updates found.")
        return

    for _, task in tasks_df.iterrows():
        with st.container(border=True):
            title_col, meta_col, action_col = st.columns([4, 3, 1])
            with title_col:
                st.markdown(f"**{task['title']}**")
                update_preview = task.get("latest_update") or "No update yet."
                st.caption(update_preview[:120] + ("..." if len(update_preview) > 120 else ""))
            with meta_col:
                st.markdown(status_badge(task.get("status")), unsafe_allow_html=True)
                st.caption(
                    f"{task.get('owner_primary') or 'Unassigned'} | "
                    f"{task.get('team') or 'No team'} | "
                    f"{due_label(task.get('due_date'), task.get('status'))}"
                )
            with action_col:
                if st.button("Details", key=f"toggle_task_details_{task['id']}", use_container_width=True):
                    current = st.session_state.get("open_task_id")
                    st.session_state["open_task_id"] = None if current == int(task["id"]) else int(task["id"])
                    st.rerun()

            expanded = st.session_state.get("open_task_id") == int(task["id"])
            with st.expander("Task details and actions", expanded=expanded):
                detail_cols = st.columns(4)
                detail_cols[0].metric("Progress", f"{int(task['progress_percent'] or 0)}%")
                detail_cols[1].write(f"**Primary:** {task.get('owner_primary') or 'Unassigned'}")
                detail_cols[2].write(f"**Secondary:** {task.get('owner_secondary') or 'None'}")
                detail_cols[3].write(f"**Team:** {task.get('team') or 'None'}")
                st.progress(int(task["progress_percent"] or 0) / 100)
                st.write(f"**Due:** {task.get('due_date') or 'No due date'} ({due_label(task.get('due_date'), task.get('status'))})")
                st.write(f"**Latest update:** {task.get('latest_update') or 'No update yet.'}")
                if task.get("notes"):
                    st.write(f"**Notes:** {task.get('notes')}")

                quick_col, full_col = st.columns([1, 1])
                with quick_col:
                    render_quick_update_popover(supabase, task)
                with full_col:
                    if st.button("Full edit", key=f"full_edit_task_{task['id']}", use_container_width=True):
                        st.session_state["selected_task_id"] = int(task["id"])
                        st.session_state["active_dialog"] = "edit_task"
                        st.rerun()


def render_project_updates_v2(
    supabase: Client,
    project_tasks: pd.DataFrame,
    project_id: int,
    users_df: pd.DataFrame,
    teams_df: pd.DataFrame,
) -> None:
    st.markdown("#### Updates")

    if project_tasks.empty:
        st.info("No task updates available.")
        return

    filter_col1, filter_col2, filter_col3, filter_col4, view_col = st.columns([1, 1, 1, 1, 1])

    team_options = ["All"] + sorted(project_tasks["team"].dropna().unique().tolist())
    owner_options = ["All"] + sorted(project_tasks["owner_primary"].dropna().unique().tolist())
    task_options = ["All"] + sorted(project_tasks["title"].dropna().unique().tolist())
    status_options = ["All"] + STATUSES

    with filter_col1:
        selected_team = st.selectbox("Team", team_options, key="project_filter_team")
    with filter_col2:
        selected_owner = st.selectbox("Owner", owner_options, key="project_filter_owner")
    with filter_col3:
        selected_task = st.selectbox("Task", task_options, key="project_filter_task")
    with filter_col4:
        selected_status = st.selectbox("Status", status_options, key="project_filter_status")
    with view_col:
        updates_view = st.segmented_control("View mode", ["Feed view", "Table view"], key="project_updates_view")

    filtered = project_tasks.copy()
    if selected_team != "All":
        filtered = filtered[filtered["team"] == selected_team]
    if selected_owner != "All":
        filtered = filtered[filtered["owner_primary"] == selected_owner]
    if selected_task != "All":
        filtered = filtered[filtered["title"] == selected_task]
    if selected_status != "All":
        filtered = filtered[filtered["status"] == selected_status]

    filtered = smart_sort_tasks(filtered)

    if updates_view == "Table view":
        render_task_table(filtered)
    else:
        render_task_feed(supabase, filtered, project_id, users_df, teams_df)


def render_project_page_v2(
    supabase: Client,
    projects_df: pd.DataFrame,
    tasks_df: pd.DataFrame,
    users_df: pd.DataFrame,
    teams_df: pd.DataFrame,
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
    project_tasks = tasks_df[tasks_df["project"] == project_name].copy() if not tasks_df.empty else pd.DataFrame(columns=TASK_COLUMNS)
    progress = project_progress(project_tasks)
    blocked_count = int((project_tasks["status"] == "Blocked").sum()) if not project_tasks.empty else 0
    done_count = int((project_tasks["status"] == "Done").sum()) if not project_tasks.empty else 0

    back_col, title_col, action_col1, action_col2 = st.columns([1, 5, 1, 1])
    with back_col:
        if st.button("Back", use_container_width=True):
            st.session_state["page"] = "Home"
            st.session_state["selected_task_id"] = None
            st.session_state["active_dialog"] = None
            st.rerun()
    with title_col:
        st.subheader(project_name)
        st.caption(project_row.get("description", "") or "Project detail view")
    with action_col1:
        if st.button("Edit Project", use_container_width=True):
            st.session_state["active_dialog"] = "edit_project"
            st.rerun()
    with action_col2:
        if st.button("Add Task", use_container_width=True):
            st.session_state["active_dialog"] = "add_task"
            st.rerun()

    metric_cols = st.columns(5)
    metric_cols[0].metric("Due date", project_row["due_date"] or "None")
    metric_cols[1].metric("Progress", f"{progress}%")
    metric_cols[2].metric("Tasks", len(project_tasks))
    metric_cols[3].metric("Blocked", blocked_count)
    metric_cols[4].metric("Done", done_count)
    st.progress(progress / 100)

    st.divider()
    render_project_timeline(project_tasks)

    st.divider()
    render_project_updates_v2(supabase, project_tasks, int(project_row["id"]), users_df, teams_df)

    active_dialog = st.session_state.get("active_dialog")
    if active_dialog == "edit_project":
        @st.dialog("Edit project")
        def project_dialog() -> None:
            render_project_edit_dialog(supabase, project_row)

        project_dialog()
    elif active_dialog == "add_task":
        @st.dialog("Add task")
        def add_task_dialog() -> None:
            render_add_task_dialog(supabase, int(project_row["id"]), users_df, teams_df)

        add_task_dialog()
    elif active_dialog == "edit_task":
        selected_task_id = st.session_state.get("selected_task_id")
        task_match = tasks_df[tasks_df["id"] == selected_task_id] if selected_task_id else pd.DataFrame()
        if not task_match.empty:
            @st.dialog("Edit task")
            def edit_task_dialog() -> None:
                render_task_edit_dialog(supabase, task_match.iloc[0], int(project_row["id"]), users_df, teams_df)

            edit_task_dialog()
        else:
            st.session_state["active_dialog"] = None


def render_project_page(
    supabase: Client,
    projects_df: pd.DataFrame,
    tasks_df: pd.DataFrame,
    users_df: pd.DataFrame,
    teams_df: pd.DataFrame,
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
    teams_table_missing = teams_df.attrs.get("missing_table", False)
    team_names = sorted(teams_df["name"].dropna().tolist()) if "name" in teams_df.columns else []

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
        add_title_col, add_back_col = st.columns([5, 1])
        with add_title_col:
            st.markdown("#### Add task to this project")
        with add_back_col:
            if st.button("Back to project", key="back_from_add_task", use_container_width=True):
                st.session_state["task_mode"] = None
                st.rerun()

        with st.form("project_page_add_task_form", clear_on_submit=True):
            title = st.text_input("Task title")
            if teams_table_missing:
                team = st.text_input("Team", help="Create a `teams` table to manage team values from Settings.")
            else:
                team = st.selectbox(
                    "Team",
                    [""] + team_names,
                    format_func=lambda x: x or "None",
                )
            selected_primary = st.selectbox("Primary owner", list(user_map.keys()) if user_map else [None])
            selected_secondary = st.selectbox("Secondary owner", [None] + list(user_map.keys()), format_func=lambda x: x or "None")
            progress_percent = st.slider("Progress %", 0, 100, 0)
            start_date = st.date_input("Start date", value=None)
            due_date = st.date_input("Due date", value=None)
            status = st.selectbox("Status", STATUSES)
            latest_update = st.text_area("Latest progress update")
            notes = st.text_area("Notes")

            submitted = st.form_submit_button("Create task")
            if submitted and title.strip() and selected_primary:
                try:
                    add_task(
                        supabase,
                        title.strip(),
                        team.strip() if isinstance(team, str) else (team or None),
                        int(project_row["id"]),
                        user_map.get(selected_primary),
                        user_map.get(selected_secondary) if selected_secondary else None,
                        progress_percent,
                        start_date.isoformat() if start_date else None,
                        due_date.isoformat() if due_date else None,
                        status,
                        latest_update.strip(),
                        notes.strip(),
                    )
                except RuntimeError as exc:
                    st.error(f"{exc} Run: `alter table public.tasks add column if not exists start_date date;`")
                else:
                    st.success("Task created.")
                    st.rerun()

    if task_mode == "edit":
        st.divider()
        edit_title_col, edit_back_col = st.columns([5, 1])
        with edit_title_col:
            st.markdown("#### Edit task")
        with edit_back_col:
            if st.button("Back to project", key="back_from_edit_task", use_container_width=True):
                st.session_state["task_mode"] = None
                st.rerun()

        if project_tasks.empty:
            st.info("No tasks available in this project.")
        else:
            task_options = {f"#{row['id']} - {row['title']}": row for _, row in project_tasks.iterrows()}
            selected_label = st.selectbox("Select task", list(task_options.keys()), key="project_task_editor")
            selected_task = task_options[selected_label]

            current_primary_name = next((name for name, uid in user_map.items() if uid == selected_task["owner_primary_id"]), None)
            current_secondary_name = next((name for name, uid in user_map.items() if uid == selected_task["owner_secondary_id"]), None)
            current_team_name = selected_task["team"] or ""

            with st.form("project_page_edit_task_form"):
                edit_title = st.text_input("Task title", value=selected_task["title"] or "")
                if teams_table_missing:
                    edit_team = st.text_input(
                        "Team",
                        value=current_team_name,
                        help="Create a `teams` table to manage team values from Settings.",
                    )
                else:
                    team_choices = [""] + sorted(set(team_names + ([current_team_name] if current_team_name else [])))
                    edit_team = st.selectbox(
                        "Team",
                        team_choices,
                        index=team_choices.index(current_team_name) if current_team_name in team_choices else 0,
                        format_func=lambda x: x or "None",
                    )
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
                existing_start = pd.to_datetime(selected_task["start_date"], errors="coerce")
                edit_start = st.date_input("Start date", value=existing_start.date() if pd.notna(existing_start) else None)
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
                    try:
                        update_task_full(
                            supabase,
                            int(selected_task["id"]),
                            edit_title.strip(),
                            edit_team.strip() if isinstance(edit_team, str) else (edit_team or None),
                            int(project_row["id"]),
                            user_map.get(edit_primary_owner),
                            user_map.get(edit_secondary_owner) if edit_secondary_owner else None,
                            edit_progress,
                            edit_start.isoformat() if edit_start else None,
                            edit_due.isoformat() if edit_due else None,
                            edit_status,
                            edit_latest_update.strip(),
                            edit_notes.strip(),
                        )
                    except RuntimeError as exc:
                        st.error(f"{exc} Run: `alter table public.tasks add column if not exists start_date date;`")
                    else:
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


def render_settings(
    supabase: Client,
    users_df: pd.DataFrame,
    teams_df: pd.DataFrame,
    tasks_df: pd.DataFrame,
) -> None:
    back_col, title_col = st.columns([1, 5])
    with back_col:
        if st.button("← Home", use_container_width=True):
            st.session_state["page"] = "Home"
            st.rerun()
    with title_col:
        st.subheader("Settings")
        st.caption("Manage users, teams, and exports")

    tab_users, tab_teams, tab_exports = st.tabs(["Users", "Teams", "Export"])

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

    with tab_teams:
        if teams_df.attrs.get("missing_table", False):
            st.warning(
                "The `teams` table was not found yet. Add it in Supabase SQL first, then this tab can manage team values."
            )
            st.code(
                "create table if not exists public.teams (\n"
                "  id bigint generated by default as identity primary key,\n"
                "  name text not null unique\n"
                ");"
            )
        else:
            st.markdown("#### Add team")
            with st.form("add_team_form", clear_on_submit=True):
                team_name = st.text_input("Team name")
                submitted = st.form_submit_button("Add team")
                if submitted and team_name.strip():
                    add_team(supabase, team_name.strip())
                    st.success("Team added.")
                    st.rerun()

            st.markdown("#### Edit or delete team")
            if teams_df.empty:
                st.info("No teams available.")
            else:
                team_options = {f"#{row['id']} - {row['name']}": row for _, row in teams_df.iterrows()}
                selected_label = st.selectbox("Select team", list(team_options.keys()), key="settings_team_editor")
                selected_team = team_options[selected_label]

                with st.form("edit_team_form"):
                    edit_team_name = st.text_input("Team name", value=selected_team["name"] or "")
                    confirm_delete_team = st.checkbox("I confirm I want to permanently delete this team")
                    save_team = st.form_submit_button("Save team changes")
                    delete_team_btn = st.form_submit_button("Delete team")

                    if save_team and edit_team_name.strip():
                        update_team(
                            supabase,
                            int(selected_team["id"]),
                            selected_team["name"],
                            edit_team_name.strip(),
                        )
                        st.success("Team updated.")
                        st.rerun()

                    if delete_team_btn:
                        if not confirm_delete_team:
                            st.error("Please check the confirmation box before deleting this team.")
                        else:
                            delete_team(
                                supabase,
                                int(selected_team["id"]),
                                selected_team["name"],
                            )
                            st.success("Team deleted.")
                            st.rerun()

            if not teams_df.empty:
                st.markdown("#### Current teams")
                st.dataframe(teams_df, use_container_width=True, hide_index=True)

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
                    "start_date",
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
    if "global_view_mode" not in st.session_state:
        st.session_state["global_view_mode"] = "All tasks"
    if "project_updates_view" not in st.session_state:
        st.session_state["project_updates_view"] = "Feed view"
    if "active_dialog" not in st.session_state:
        st.session_state["active_dialog"] = None
    if "selected_task_id" not in st.session_state:
        st.session_state["selected_task_id"] = None

    supabase = get_supabase()
    users_df = fetch_users(supabase)
    teams_df = fetch_teams(supabase)
    projects_df = fetch_projects(supabase)
    tasks_df = fetch_tasks(supabase)

    render_top_bar()
    st.divider()

    page = st.session_state.get("page", "Home")
    if page == "Settings":
        render_settings(supabase, users_df, teams_df, tasks_df)
    elif page == "Project":
        render_project_page_v2(supabase, projects_df, tasks_df, users_df, teams_df)
    else:
        render_home_v2(supabase, projects_df, tasks_df)


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
# create table if not exists public.teams (
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
#   start_date date,
#   due_date date,
#   status text not null default 'Not Started',
#   latest_update text,
#   notes text,
#   updated_at timestamptz not null default now()
# );
#
# If your tasks table already exists, run this too:
# alter table public.tasks add column if not exists team text;
# alter table public.tasks add column if not exists start_date date;
