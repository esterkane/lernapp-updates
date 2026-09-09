"""Task ownership for local workspaces; legacy tasks stay with the default workspace."""

import sqlalchemy as sa
from alembic import op

revision = "0004_task_workspace"
down_revision = "0003_usage_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("learner_id", sa.String(64), nullable=True))
    op.create_foreign_key("fk_tasks_learner", "tasks", "learners", ["learner_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_tasks_learner_id", "tasks", ["learner_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_learner_id", table_name="tasks")
    op.drop_constraint("fk_tasks_learner", "tasks", type_="foreignkey")
    op.drop_column("tasks", "learner_id")
