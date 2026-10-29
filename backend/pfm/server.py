import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import core

AccountType = Literal["personal", "business", "kids"]
FlowType = Literal["income", "expense", "transfer"]
RuleField = Literal["merchant", "description", "memo"]
MatchMode = Literal["contains", "word", "exact"]

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
logger = logging.getLogger("pfm")


class ImportCommit(BaseModel):
    account_type: AccountType


class BackupCreate(BaseModel):
    label: str = Field(default="", max_length=40)
    automatic: bool = False


class CategoryCreate(BaseModel):
    account_type: AccountType
    name: str = Field(min_length=1, max_length=80)


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    is_income: Optional[bool] = None


class CategoryMerge(BaseModel):
    account_type: AccountType
    source_id: int
    target_id: int


class CategoryRuleInput(BaseModel):
    account_type: AccountType
    category_id: int
    match_text: str = Field(min_length=1, max_length=120)
    match_fields: list[RuleField] = Field(min_length=1)
    flow_type: FlowType
    match_mode: MatchMode = "contains"


class CategoryRulePreview(BaseModel):
    account_type: AccountType
    match_text: str = Field(min_length=1, max_length=120)
    match_fields: list[RuleField] = Field(min_length=1)
    legacy_normalized: bool = False
    rule_id: Optional[int] = None
    match_mode: MatchMode = "contains"


class CategoryRuleImpact(BaseModel):
    account_type: AccountType
    match_text: str = Field(min_length=1, max_length=120)
    match_fields: list[RuleField] = Field(min_length=1)
    match_mode: MatchMode = "contains"
    category_id: int
    flow_type: FlowType
    rule_id: Optional[int] = None


class CategoryRuleOrder(BaseModel):
    account_type: AccountType
    rule_ids: list[int]


class AccountTypePayload(BaseModel):
    account_type: AccountType


class TransactionUpdate(BaseModel):
    flow_type: Optional[FlowType] = None
    category_id: Optional[int] = None


class TransactionAnnotation(BaseModel):
    note: Optional[str] = None
    follow_up: Optional[Literal["open", "done"]] = None


class BulkCategory(BaseModel):
    account_type: AccountType
    transaction_ids: list[int] = Field(min_length=1, max_length=5000)
    category_id: int


class BudgetInput(BaseModel):
    account_type: AccountType
    category_id: int
    year: str
    # Belgian or plain notation ("1.500", "1500,50"); 0, empty or null removes the budget.
    amount: Union[str, int, float, None] = None


class RecurringCreate(BaseModel):
    account_type: AccountType
    merchant: str = Field(min_length=1)
    kind: str
    status: Literal["confirmed", "ignored"]
    cadence: Optional[Literal["monthly", "quarterly", "yearly"]] = None
    category_id: Optional[int] = None
    amount: Union[str, int, float, None] = None
    day_of_month: Optional[int] = None
    end_date: Optional[str] = None
    note: Optional[str] = None


class RecurringManual(BaseModel):
    account_type: AccountType
    merchant: str = Field(min_length=1)
    cadence: Literal["monthly", "quarterly", "yearly"]
    amount: Union[str, int, float]
    start_date: str
    day_of_month: Optional[int] = None
    end_date: Optional[str] = None
    category_id: Optional[int] = None
    note: Optional[str] = None
    kind: str = ""


class RecurringAmountInput(BaseModel):
    effective_date: str
    amount: Union[str, int, float]


class RecurringUpdate(BaseModel):
    account_type: AccountType
    status: Optional[Literal["confirmed", "ignored"]] = None
    cadence: Optional[Literal["monthly", "quarterly", "yearly"]] = None
    day_of_month: Optional[int] = None
    category_id: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None  # null clears it
    note: Optional[str] = None
    amounts: Optional[list[RecurringAmountInput]] = None


class RecurringLink(BaseModel):
    account_type: AccountType
    linked_item_id: Optional[int] = None


class ForecastSettingsUpdate(BaseModel):
    personal_min_balance: Union[str, int, float, None] = None
    business_buffer: Union[str, int, float, None] = None
    payment_delay_months: Optional[int] = None
    vat_rate: Optional[float] = None
    vat_delay_months: Optional[int] = None
    vat_day: Optional[int] = None
    baseline_months: Optional[int] = None
    baseline_weighted: Optional[bool] = None
    one_off_factor: Optional[float] = None
    salary_net_percent: Optional[float] = None
    reimbursement_delay_days: Optional[int] = None
    tax_reduced_threshold: Union[str, int, float, None] = None
    tax_reduced_rate: Optional[float] = None
    tax_reduced_band: Union[str, int, float, None] = None
    tax_standard_rate: Optional[float] = None
    tax_base_rate: Optional[float] = None
    tax_surcharge_applies: Optional[bool] = None
    tax_profit_excluded: Optional[list[int]] = None
    loan_start: Optional[str] = None
    loan_interest_rate: Optional[float] = None
    closed_categories: Optional[dict[AccountType, list[int]]] = None
    roles: Optional[dict[str, Optional[int]]] = None  # a null category clears the role


class LoanInterestCreate(BaseModel):
    date: str
    amount: Union[str, int, float]
    note: Optional[str] = None


class ClaimCreate(BaseModel):
    date: Optional[str] = None
    description: Optional[str] = None
    amount: Union[str, int, float, None] = None
    source_transaction_id: Optional[int] = None
    share: Optional[float] = None
    personal_category_id: Optional[int] = None
    business_category_id: Optional[int] = None
    expected_date: Optional[str] = None
    note: Optional[str] = None


class ClaimUpdate(BaseModel):
    date: Optional[str] = None
    description: Optional[str] = None
    amount: Union[str, int, float, None] = None
    personal_category_id: Optional[int] = None  # null clears it
    business_category_id: Optional[int] = None  # null clears it
    expected_date: Optional[str] = None  # null clears it
    paid_transaction_id: Optional[int] = None  # an id marks the claim paid, null reopens it
    note: Optional[str] = None


class SalaryInput(BaseModel):
    monthly_amount: Union[str, int, float]
    last_month: int
    minimum_amount: Union[str, int, float, None] = None


class DayRateInput(BaseModel):
    rate: Union[str, int, float]


class DayOffInput(BaseModel):
    start_date: str
    end_date: str
    fraction: float = 1
    note: str = ""
    repeat_weeks: int = 0
    repeat_until: Optional[str] = None


class PlannedCreate(BaseModel):
    account_type: AccountType
    date: str
    amount: Union[str, int, float]
    category_id: Optional[int] = None
    note: Optional[str] = None


class PlannedUpdate(BaseModel):
    account_type: AccountType
    date: Optional[str] = None
    amount: Union[str, int, float, None] = None
    category_id: Optional[int] = None  # null clears it
    note: Optional[str] = None


class PlannedLink(BaseModel):
    account_type: AccountType
    linked_item_id: Optional[int] = None


def _validate_period(month: Optional[str], year: Optional[str]) -> None:
    if month is not None and (len(month) != 7 or month[4] != "-"):
        raise HTTPException(status_code=400, detail="Month must use YYYY-MM format")
    if year is not None and (len(year) != 4 or not year.isdigit()):
        raise HTTPException(status_code=400, detail="Year must use YYYY format")
    if month is not None and year is not None:
        raise HTTPException(status_code=400, detail="Choose either a month or a year")


SAFETY_BACKUP_INTERVAL = timedelta(minutes=30)


def create_app(database_path: Optional[Path] = None, frontend_dist: Path = FRONTEND_DIST) -> FastAPI:
    """Build the app for a database, creating or upgrading the database first."""
    database_path = database_path or core.default_database_path()
    backup_dir = core.default_backup_dir(database_path)
    backup = core.initialize(database_path, backup_dir)
    if backup:
        logger.warning("Upgraded the database; the previous version is saved as %s", backup)
    staging: dict[str, Any] = {}

    def safety_backup(label: str, throttled: bool = False, prune: bool = True) -> Optional[str]:
        """Back up before a risky action; fail the request if the backup cannot be made.

        A throttled backup is skipped when any backup is newer than SAFETY_BACKUP_INTERVAL.
        Returns the new backup's name, or None when it was skipped.
        """
        try:
            if throttled:
                backups = core.list_backups(database_path, backup_dir)
                if backups and datetime.now() - datetime.fromisoformat(backups[0]["created_at"]) < SAFETY_BACKUP_INTERVAL:
                    return None
            name = core.backup_database(database_path, label, backup_dir).name
        except Exception as error:
            logger.exception("Backup %s failed", label)
            raise HTTPException(
                status_code=500, detail=f"Could not back up {label}; nothing was changed."
            ) from error
        if prune:
            prune_backups()
        return name

    def prune_backups() -> None:
        """Keep only the latest automatic backups; a failure here never fails the action."""
        try:
            core.prune_automatic_backups(database_path, backup_dir=backup_dir)
        except Exception:
            logger.exception("Could not prune old automatic backups")

    app = FastAPI(title="Personal Finance Manager", docs_url=None, redoc_url=None)

    @app.exception_handler(LookupError)
    def not_found(_: Request, error: LookupError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(ValueError)
    def bad_request(_: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/accounts/overview")
    def get_accounts_overview() -> list[dict[str, Any]]:
        return core.account_overview(database_path)

    @app.get("/api/accounts/{account_id}/statements")
    def get_card_statements(account_id: int) -> list[dict[str, Any]]:
        return core.card_statements(database_path, account_id)

    @app.get("/api/transactions/{transaction_id}/statement")
    def get_settlement_statement(transaction_id: int) -> dict[str, Any]:
        return core.settlement_statement(database_path, transaction_id)

    @app.get("/api/accounts")
    def get_accounts() -> list[dict[str, Any]]:
        return core.accounts_list(database_path)

    @app.get("/api/categories")
    def get_categories(account_type: AccountType) -> list[dict[str, Any]]:
        return core.category_list(database_path, account_type)

    @app.get("/api/category-management")
    def get_category_management(account_type: AccountType) -> list[dict[str, Any]]:
        return core.category_management(database_path, account_type)

    @app.post("/api/categories", status_code=201)
    def create_category(payload: CategoryCreate) -> dict[str, Any]:
        return core.add_category(database_path, payload.account_type, payload.name)

    @app.patch("/api/categories/{category_id}")
    def update_category(category_id: int, payload: CategoryUpdate) -> dict[str, str]:
        if payload.name is None and payload.is_income is None:
            raise ValueError("Send a name or is_income")
        if payload.name is not None:
            core.rename_category(database_path, category_id, payload.name)
        if payload.is_income is not None:
            core.set_category_income(database_path, category_id, payload.is_income)
        return {"status": "updated"}

    @app.delete("/api/categories/{category_id}")
    def remove_category(category_id: int) -> dict[str, str]:
        safety_backup("before removing category")
        core.remove_category(database_path, category_id)
        return {"status": "deleted"}

    @app.post("/api/categories/merge")
    def merge_category(payload: CategoryMerge) -> dict[str, str]:
        safety_backup("before merging categories")
        core.merge_categories(database_path, payload.account_type, payload.source_id, payload.target_id)
        return {"status": "merged"}

    @app.post("/api/category-rules/preview")
    def preview_category_rule(payload: CategoryRulePreview) -> dict[str, Any]:
        return core.preview_category_rule(
            database_path,
            payload.account_type,
            payload.match_text,
            payload.match_fields,
            payload.legacy_normalized,
            payload.rule_id,
            payload.match_mode,
        )

    @app.post("/api/category-rules/impact")
    def category_rule_impact(payload: CategoryRuleImpact) -> dict[str, Any]:
        return core.rule_impact(
            database_path,
            payload.account_type,
            payload.match_text,
            payload.match_fields,
            payload.match_mode,
            payload.category_id,
            payload.flow_type,
            payload.rule_id,
        )

    @app.post("/api/category-rules", status_code=201)
    def create_category_rule(payload: CategoryRuleInput) -> dict[str, Any]:
        return core.save_category_rule(
            database_path,
            payload.account_type,
            payload.category_id,
            payload.match_text,
            payload.match_fields,
            payload.flow_type,
            match_mode=payload.match_mode,
        )

    @app.put("/api/category-rules/order")
    def update_category_rule_order(payload: CategoryRuleOrder) -> dict[str, str]:
        core.reorder_category_rules(database_path, payload.account_type, payload.rule_ids)
        return {"status": "updated"}

    @app.put("/api/category-rules/{rule_id}")
    def update_category_rule(rule_id: int, payload: CategoryRuleInput) -> dict[str, Any]:
        return core.save_category_rule(
            database_path,
            payload.account_type,
            payload.category_id,
            payload.match_text,
            payload.match_fields,
            payload.flow_type,
            rule_id,
            payload.match_mode,
        )

    @app.delete("/api/category-rules/{rule_id}")
    def remove_category_rule(rule_id: int, account_type: AccountType) -> dict[str, str]:
        safety_backup("before deleting rule", throttled=True)
        core.delete_category_rule(database_path, account_type, rule_id)
        return {"status": "deleted"}

    @app.post("/api/category-rules/{rule_id}/run")
    def run_category_rule(rule_id: int, payload: AccountTypePayload) -> dict[str, int]:
        safety_backup("before applying rules", throttled=True)
        return core.run_category_rule(database_path, payload.account_type, rule_id)

    @app.post("/api/import/preview")
    async def import_preview(request: Request, filename: str = "bank-export.csv") -> dict[str, Any]:
        too_large = HTTPException(status_code=413, detail="CSV exceeds the 10 MB import limit")
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > core.MAX_IMPORT_BYTES:
            raise too_large
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > core.MAX_IMPORT_BYTES:
                raise too_large
        return core.stage_import(database_path, filename, bytes(content), staging)

    @app.post("/api/import/{token}/commit")
    def import_commit(token: str, payload: ImportCommit) -> dict[str, Any]:
        try:
            return core.commit_import(database_path, token, payload.account_type, staging)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.delete("/api/import/{token}/cancel", status_code=204)
    def cancel_import(token: str) -> None:
        core.cancel_staged_import(token, staging)

    @app.get("/api/imports")
    def get_imports(account_type: AccountType) -> list[dict[str, Any]]:
        return core.import_history(database_path, account_type)

    @app.delete("/api/imports/{import_id}")
    def undo_import(import_id: int) -> dict[str, Any]:
        safety_backup("before undoing import")
        return core.undo_import(database_path, import_id)

    @app.get("/api/backups")
    def get_backups() -> list[dict[str, Any]]:
        return core.list_backups(database_path, backup_dir)

    @app.post("/api/backups", status_code=201)
    def create_backup(payload: BackupCreate) -> dict[str, Any]:
        if payload.automatic and payload.label not in core.AUTOMATIC_BACKUP_LABELS:
            raise HTTPException(status_code=400, detail="That note is not an automatic backup note")
        if not payload.automatic and core.is_reserved_note(payload.label):
            raise HTTPException(status_code=400, detail="That note is reserved for automatic backups")
        name = core.backup_database(database_path, payload.label, backup_dir).name
        if payload.automatic:
            prune_backups()
        return next(item for item in core.list_backups(database_path, backup_dir) if item["name"] == name)

    @app.post("/api/backups/{name}/restore")
    def restore_backup(name: str) -> dict[str, Any]:
        if name not in {item["name"] for item in core.list_backups(database_path, backup_dir)}:
            raise LookupError("Backup not found")
        # Prune only after restoring, so the chosen backup cannot be pruned away first.
        safety = safety_backup("before restoring", prune=False)
        restored = core.restore_backup(database_path, name, backup_dir)
        prune_backups()
        staging.clear()
        return {"restored": restored, "safety_backup": safety}

    @app.get("/api/budgets")
    def get_budgets(account_type: AccountType, year: str) -> list[dict[str, Any]]:
        _validate_period(None, year)
        return core.list_budgets(database_path, account_type, year)

    @app.put("/api/budgets")
    def put_budget(payload: BudgetInput) -> dict[str, Any]:
        # No safety backup: one explicit single-row edit.
        return core.set_budget(database_path, payload.account_type, payload.category_id, payload.year, payload.amount)

    @app.get("/api/recurring")
    def get_recurring(account_type: AccountType) -> dict[str, Any]:
        return core.list_recurring(database_path, account_type)

    @app.get("/api/recurring/linkable")
    def get_linkable_recurring(account_type: AccountType) -> list[dict[str, Any]]:
        return core.linkable_recurring(database_path, account_type)

    @app.post("/api/recurring")
    def post_recurring(payload: RecurringCreate) -> dict[str, Any]:
        # No safety backup: a single explicit row.
        return core.confirm_recurring(
            database_path, payload.account_type, payload.merchant, payload.kind, payload.status,
            cadence=payload.cadence, category_id=payload.category_id, amount=payload.amount,
            day_of_month=payload.day_of_month, end_date=payload.end_date, note=payload.note,
        )

    @app.post("/api/recurring/manual")
    def post_manual_recurring(payload: RecurringManual) -> dict[str, Any]:
        # No safety backup: a single explicit row.
        return core.add_recurring(
            database_path, payload.account_type, payload.merchant, payload.cadence, payload.amount, payload.start_date,
            day_of_month=payload.day_of_month, end_date=payload.end_date, category_id=payload.category_id,
            note=payload.note, kind=payload.kind,
        )

    @app.get("/api/recurring/groups")
    def get_recurring_groups(account_type: AccountType) -> list[dict[str, Any]]:
        return core.recurring_groups(database_path, account_type)

    @app.patch("/api/recurring/{item_id}")
    def patch_recurring(item_id: int, payload: RecurringUpdate) -> dict[str, Any]:
        changes = payload.model_dump(exclude_unset=True, exclude={"account_type"})
        # A null status, cadence, start date, note or amounts means "unchanged"; only end date and category may clear.
        changes = {k: v for k, v in changes.items() if v is not None or k in ("end_date", "category_id", "day_of_month")}
        return core.update_recurring(database_path, payload.account_type, item_id, changes)

    @app.delete("/api/recurring/{item_id}")
    def delete_recurring(item_id: int, account_type: AccountType) -> dict[str, str]:
        core.delete_recurring(database_path, account_type, item_id)
        return {"status": "deleted"}

    @app.put("/api/recurring/{item_id}/link")
    def put_recurring_link(item_id: int, payload: RecurringLink) -> dict[str, Any]:
        return core.link_recurring(database_path, payload.account_type, item_id, payload.linked_item_id)

    @app.get("/api/forecast")
    def get_forecast(account_type: AccountType) -> dict[str, Any]:
        return core.forecast(database_path)[account_type]

    @app.get("/api/forecast/consulting")
    def get_consulting_payments() -> dict[str, Any]:
        return core.consulting_payments(database_path)

    @app.get("/api/forecast/corporate-tax")
    def get_corporate_tax() -> dict[str, Any]:
        return core.corporate_tax(database_path)

    @app.post("/api/forecast/corporate-tax/{year}/apply")
    def post_corporate_tax_apply(year: str) -> dict[str, Any]:
        # No safety backup: replaces planned rows only.
        return core.apply_corporate_tax(database_path, year)

    @app.get("/api/forecast/director-loan")
    def get_director_loan() -> dict[str, Any]:
        return core.director_loan(database_path)

    @app.post("/api/forecast/director-loan/interest")
    def post_director_loan_interest(payload: LoanInterestCreate) -> dict[str, Any]:
        # No safety backup: adds planned rows only.
        return core.plan_loan_interest(database_path, payload.date, payload.amount, payload.note)

    @app.get("/api/forecast/settings")
    def get_forecast_settings() -> dict[str, Any]:
        return core.get_settings(database_path)

    @app.put("/api/forecast/settings")
    def put_forecast_settings(payload: ForecastSettingsUpdate) -> dict[str, Any]:
        # No safety backup: a few explicit settings. A null means "unchanged", except a role's category.
        changes = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        return core.set_settings(database_path, changes)

    @app.get("/api/forecast/salary")
    def get_salary() -> list[dict[str, Any]]:
        return core.list_salary(database_path)

    @app.put("/api/forecast/salary/{year}")
    def put_salary(year: str, payload: SalaryInput) -> dict[str, Any]:
        return core.set_salary(database_path, year, payload.monthly_amount, payload.last_month, payload.minimum_amount)

    @app.delete("/api/forecast/salary/{year}")
    def delete_salary(year: str) -> dict[str, str]:
        core.delete_salary(database_path, year)
        return {"status": "deleted"}

    @app.get("/api/forecast/day-rates")
    def get_day_rates() -> list[dict[str, Any]]:
        return core.list_day_rates(database_path)

    @app.put("/api/forecast/day-rates/{effective_date}")
    def put_day_rate(effective_date: str, payload: DayRateInput) -> dict[str, Any]:
        return core.set_day_rate(database_path, effective_date, payload.rate)

    @app.delete("/api/forecast/day-rates/{effective_date}")
    def delete_day_rate(effective_date: str) -> dict[str, str]:
        core.delete_day_rate(database_path, effective_date)
        return {"status": "deleted"}

    @app.get("/api/forecast/days-off")
    def get_days_off() -> list[dict[str, Any]]:
        return core.list_days_off(database_path)

    @app.post("/api/forecast/days-off")
    def post_day_off(payload: DayOffInput) -> dict[str, Any]:
        return core.add_day_off(
            database_path, payload.start_date, payload.end_date, payload.fraction, payload.note,
            payload.repeat_weeks, payload.repeat_until,
        )

    @app.delete("/api/forecast/days-off/{item_id}")
    def delete_day_off(item_id: int) -> dict[str, str]:
        core.delete_day_off(database_path, item_id)
        return {"status": "deleted"}

    @app.get("/api/planned")
    def get_planned(account_type: AccountType) -> list[dict[str, Any]]:
        return core.list_planned(database_path, account_type)

    @app.post("/api/planned")
    def post_planned(payload: PlannedCreate) -> dict[str, Any]:
        return core.add_planned(
            database_path, payload.account_type, payload.date, payload.amount, payload.category_id, payload.note
        )

    @app.patch("/api/planned/{item_id}")
    def patch_planned(item_id: int, payload: PlannedUpdate) -> dict[str, Any]:
        changes = payload.model_dump(exclude_unset=True, exclude={"account_type"})
        # A null date, amount or note means "unchanged"; only the category may clear.
        changes = {k: v for k, v in changes.items() if v is not None or k == "category_id"}
        return core.update_planned(database_path, payload.account_type, item_id, changes)

    @app.delete("/api/planned/{item_id}")
    def delete_planned(item_id: int, account_type: AccountType) -> dict[str, str]:
        core.delete_planned(database_path, account_type, item_id)
        return {"status": "deleted"}

    @app.put("/api/planned/{item_id}/link")
    def put_planned_link(item_id: int, payload: PlannedLink) -> dict[str, Any]:
        return core.link_planned(database_path, payload.account_type, item_id, payload.linked_item_id)

    @app.get("/api/reimbursements")
    def get_reimbursements() -> dict[str, Any]:
        return core.list_claims(database_path)

    @app.post("/api/reimbursements")
    def post_reimbursement(payload: ClaimCreate) -> dict[str, Any]:
        return core.add_claim(database_path, payload.model_dump(exclude_unset=True))

    @app.patch("/api/reimbursements/{claim_id}")
    def patch_reimbursement(claim_id: int, payload: ClaimUpdate) -> dict[str, Any]:
        changes = payload.model_dump(exclude_unset=True)
        # A null date, description, amount or note means "unchanged"; the categories, expected date and payment may clear.
        clearable = {"personal_category_id", "business_category_id", "expected_date", "paid_transaction_id"}
        changes = {k: v for k, v in changes.items() if v is not None or k in clearable}
        return core.update_claim(database_path, claim_id, changes)

    @app.delete("/api/reimbursements/{claim_id}")
    def delete_reimbursement(claim_id: int) -> dict[str, str]:
        core.delete_claim(database_path, claim_id)
        return {"status": "deleted"}

    @app.get("/api/report")
    def get_report(account_type: AccountType) -> dict[str, Any]:
        return core.report(database_path, account_type)

    @app.get("/api/balance-history")
    def get_balance_history(
        account_type: AccountType, month: Optional[str] = None, year: Optional[str] = None
    ) -> dict[str, Any]:
        _validate_period(month, year)
        return core.balance_history(database_path, account_type, month, year)

    @app.get("/api/transactions")
    def get_transactions(
        account_type: AccountType,
        month: Optional[str] = None,
        year: Optional[str] = None,
        category_id: Optional[str] = None,
        flow_type: Optional[Literal["income", "expense", "own_transfer"]] = None,
        follow_up: Optional[Literal["open", "done"]] = None,
        statement_id: Optional[int] = None,
        q: str = "",
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        _validate_period(month, year)
        if category_id is not None and category_id != "uncategorized" and not category_id.isdigit():
            raise HTTPException(status_code=400, detail="Category must be an id or 'uncategorized'")
        return core.list_transactions(
            database_path,
            account_type,
            month,
            year,
            q.strip()[:120],
            min(max(limit, 1), 1000),
            max(offset, 0),
            category_id,
            flow_type,
            follow_up,
            statement_id,
        )

    @app.get("/api/uncategorized-groups")
    def get_uncategorized_groups(account_type: AccountType) -> list[dict[str, Any]]:
        return core.uncategorized_groups(database_path, account_type)

    @app.post("/api/transactions/bulk-category")
    def bulk_category(payload: BulkCategory) -> dict[str, int]:
        return core.bulk_set_category(
            database_path, payload.account_type, payload.transaction_ids, payload.category_id
        )

    @app.get("/api/transactions/{transaction_id}/similar")
    def get_similar_transactions(transaction_id: int) -> dict[str, Any]:
        return core.similar_transactions(database_path, transaction_id)

    @app.patch("/api/transactions/{transaction_id}")
    def edit_transaction(transaction_id: int, payload: TransactionUpdate) -> dict[str, str]:
        core.update_transaction(database_path, transaction_id, payload.model_dump(exclude_unset=True))
        return {"status": "updated"}

    @app.patch("/api/transactions/{transaction_id}/annotation")
    def annotate_transaction(transaction_id: int, payload: TransactionAnnotation) -> dict[str, Any]:
        # A separate endpoint, because the category edit above marks the category as set by hand.
        return core.annotate_transaction(database_path, transaction_id, payload.model_dump(exclude_unset=True))

    if frontend_dist.is_dir():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str) -> FileResponse:
        index = frontend_dist / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="Build the frontend with npm run build")
        target = frontend_dist / path
        if path and target.is_file() and target.resolve().is_relative_to(frontend_dist.resolve()):
            return FileResponse(target)
        return FileResponse(index)

    return app
