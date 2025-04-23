"""add_is_global_disabled_field

Revision ID: 7912a1c0c46c
Revises: e6de35247b4f
Create Date: 2025-04-23 17:26:49.300348

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7912a1c0c46c'
down_revision: Union[str, None] = 'e6de35247b4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('is_global_disabled', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'is_global_disabled')
