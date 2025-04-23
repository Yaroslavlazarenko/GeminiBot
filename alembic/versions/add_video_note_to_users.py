"""add video note field to users

Revision ID: add_video_note_to_users
Revises: merge_heads_20240423
Create Date: 2024-04-23 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_video_note_to_users'
down_revision: Union[str, None] = 'merge_heads_20240423'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add responds_to_video_note column to users table
    op.add_column('users', sa.Column('responds_to_video_note', sa.Boolean(), server_default='true', nullable=False))


def downgrade() -> None:
    # Remove responds_to_video_note column from users table
    op.drop_column('users', 'responds_to_video_note') 