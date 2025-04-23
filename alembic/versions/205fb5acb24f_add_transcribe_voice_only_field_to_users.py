"""add transcribe_voice_only field to users

Revision ID: 205fb5acb24f
Revises: 2419e308fb27
Create Date: 2024-03-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '205fb5acb24f'
down_revision: Union[str, None] = '2419e308fb27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('transcribe_voice_only', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('users', 'transcribe_voice_only')
