"""make_image_data_nullable

Revision ID: 0173f8daad34
Revises: 788214780d80
Create Date: 2025-05-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0173f8daad34'
down_revision: Union[str, None] = '788214780d80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Make image_data column nullable
    with op.batch_alter_table('stickers') as batch_op:
        batch_op.alter_column('image_data',
                            existing_type=sa.LargeBinary(),
                            nullable=True)


def downgrade() -> None:
    # Make image_data column non-nullable again
    with op.batch_alter_table('stickers') as batch_op:
        batch_op.alter_column('image_data',
                            existing_type=sa.LargeBinary(),
                            nullable=False)
