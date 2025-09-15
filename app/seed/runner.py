"""EHOS seeder runner — deterministic & idempotent (Constraint H1-H4)."""

from datetime import UTC, datetime

from app.core.config import settings
from app.db.session import SessionLocal
from app.seed.audit_ingest import seed_audit_ingest
from app.seed.billing_scenarios import seed_billing_milestones
from app.seed.capa import seed_capa_scenarios
from app.seed.checklist_bank import seed_checklist_bank
from app.seed.crm_ingest import seed_crm_leads
from app.seed.crm_scenarios import seed_crm_referrals, seed_crm_scenarios
from app.seed.identity import seed_system_ingest_user
from app.seed.legacy_ingest import seed_legacy_ops
from app.seed.lost_reason_scenarios import seed_lost_reason_scenarios
from app.seed.master import seed_master
from app.seed.quotation_scenarios import seed_quotation_scenarios
from app.seed.rbac import seed_rbac
from app.seed.rfp_scenarios import seed_rfp_scenarios
from app.seed.sbm_rates import seed_sbm_rates
from app.seed.translations import seed_translations
from app.seed.users import seed_users
from app.services.billing_pipeline import remind_billing_milestones
from app.services.crm_pipeline import crm_followup_reminders
from app.services.notifications import remind_sla


async def run_seeders() -> None:
    async with SessionLocal() as session:
        await seed_master(session, data_dir=settings.seed_data_dir)
        system_user_id = await seed_system_ingest_user(session)
        await seed_audit_ingest(session, settings.audit_data_dir, system_user_id)
        await seed_crm_leads(session, settings.seed_data_dir, system_user_id)
        await seed_legacy_ops(session, settings.audit_data_dir, system_user_id)

        role_ids = await seed_rbac(session)
        resolved = await seed_users(session, role_ids)
        await seed_checklist_bank(session, resolved["root.admin@ehos.local"])
        await seed_translations(session, resolved["root.admin@ehos.local"])
        await seed_capa_scenarios(session, resolved["root.admin@ehos.local"])
        await seed_crm_scenarios(session, resolved)
        await seed_crm_referrals(session, resolved)
        await seed_rfp_scenarios(session, resolved)
        await seed_sbm_rates(session, resolved.get("corp.exec@ehos.local"))
        await seed_quotation_scenarios(session, resolved)
        await seed_lost_reason_scenarios(session, resolved)
        await seed_billing_milestones(session, resolved)
        await remind_sla(session, now=datetime.now(UTC), window_hours=24)
        await crm_followup_reminders(session, now=datetime.now(UTC))
        await remind_billing_milestones(session, now=datetime.now(UTC), days_before=14)
        await session.commit()
