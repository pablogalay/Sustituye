"""audit log of emails sent by the platform

Revision ID: 007_email_logs
Revises: 006_duty_weight
Create Date: 2026-09-17
"""
from alembic import op
import sqlalchemy as sa

revision = '007_email_logs'
down_revision = '006_duty_weight'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('email_logs',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('recipient_email', sa.String(length=255), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('sent_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('error_message', sa.Text))
    op.create_index('ix_email_logs_sent_at', 'email_logs', ['sent_at'])

def downgrade():
    op.drop_index('ix_email_logs_sent_at', table_name='email_logs')
    op.drop_table('email_logs')
