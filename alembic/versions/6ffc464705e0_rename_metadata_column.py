"""rename_metadata_column

Revision ID: 6ffc464705e0
Revises: 0d317f2b7088
Create Date: 2025-05-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '6ffc464705e0'
down_revision: Union[str, None] = '0d317f2b7088'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Add message_metadata column
    op.add_column('message_history',
                  sa.Column('message_metadata', sa.Text(), nullable=True))

def downgrade() -> None:
    # Remove message_metadata column
    op.drop_column('message_history', 'message_metadata')
