"""add transcribe_video_note field

Revision ID: add_transcribe_video_note
Revises: 7912a1c0c46c
Create Date: 2024-04-23 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_transcribe_video_note'
down_revision: Union[str, None] = '7912a1c0c46c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('transcribe_video_note', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'transcribe_video_note') 