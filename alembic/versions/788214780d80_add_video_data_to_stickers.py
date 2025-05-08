"""add_video_data_to_stickers

Revision ID: 788214780d80
Revises: 75775e318009
Create Date: 2025-05-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '788214780d80'
down_revision: Union[str, None] = '75775e318009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add video_data column to stickers table
    op.add_column('stickers',
        sa.Column('video_data', sa.LargeBinary(), nullable=True)
    )


def downgrade() -> None:
    # Remove video_data column from stickers table
    op.drop_column('stickers', 'video_data')