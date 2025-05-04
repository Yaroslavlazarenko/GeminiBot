"""add_sticker_response_settings

Revision ID: add_sticker_response
Revises: add_stickers_table
Create Date: 2025-05-04 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_sticker_response'
down_revision = 'add_stickers_table'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add responds_to_sticker to users table
    op.add_column('users',
        sa.Column('responds_to_sticker', sa.Boolean(), server_default=sa.text('true'), nullable=False)
    )

    # Add responds_to_sticker to groups table
    op.add_column('groups',
        sa.Column('responds_to_sticker', sa.Boolean(), server_default=sa.text('true'), nullable=False)
    )


def downgrade() -> None:
    # Remove responds_to_sticker from users table
    op.drop_column('users', 'responds_to_sticker')
    
    # Remove responds_to_sticker from groups table
    op.drop_column('groups', 'responds_to_sticker')