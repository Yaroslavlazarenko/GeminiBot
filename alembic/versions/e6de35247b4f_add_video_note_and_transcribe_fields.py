"""add video note and transcribe fields

Revision ID: e6de35247b4f
Revises: 2419e308fb27
Create Date: 2024-04-23 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6de35247b4f'
down_revision: Union[str, None] = '2419e308fb27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add transcribe_voice_only column to users table
    op.add_column('users', sa.Column('transcribe_voice_only', sa.Boolean(), server_default='false', nullable=False))
    
    # Add responds_to_video_note column to users table
    op.add_column('users', sa.Column('responds_to_video_note', sa.Boolean(), server_default='true', nullable=False))


def downgrade() -> None:
    # Remove responds_to_video_note column from users table
    op.drop_column('users', 'responds_to_video_note')
    
    # Remove transcribe_voice_only column from users table
    op.drop_column('users', 'transcribe_voice_only')
