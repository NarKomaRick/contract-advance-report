"""Расчётное ядро отчёта, независимое от файлов и командной строки.

Восстанавливает состояние работ и платежей на дату, учитывает прямую оплату,
распределяет общие платежи и собирает итоги договоров с проверкой сохранения денег.
Получает проверенный Dataset и возвращает Report. Все суммы в целых копейках.
"""

from collections import defaultdict
from datetime import date

from models import Contract, ContractReport, Dataset, Payment, Report, Work, WorkReport


def work_status_at(work: Work, as_of: date) -> str:
    """Определяет статус работы на выбранную дату по датам приёмки и отмены."""

    if work.cancelled_at is not None and work.cancelled_at <= as_of:
        return "cancelled"
    if work.completed_at is not None and work.completed_at <= as_of:
        return "done"
    return "planned"


def payment_amounts_at(payment: Payment, as_of: date) -> tuple[int, int]:
    """Возвращает перечисленную и возвращённую суммы на дату включительно."""
    if payment.created_at > as_of or payment.paid_at is None or payment.paid_at > as_of:
        return 0, 0
    refunded = payment.amount if payment.refunded_at is not None and payment.refunded_at <= as_of else 0
    return payment.amount, refunded


def allocate_proportionally(amount: int, capacities: dict[str, int]) -> tuple[dict[str, int], int]:
    """Распределяет сумму в пределах остатков цен, сохраняя каждую копейку.

Неделимые копейки получают работы с наибольшими дробными остатками.
Возвращает распределение по work_id и нераспределённый аванс.
"""
    if amount < 0 or any(value < 0 for value in capacities.values()):
        raise ValueError("Allocation amount and capacities must be nonnegative")
    total = sum(capacities.values())
    distributable = min(amount, total)
    allocation = dict.fromkeys(capacities, 0)
    if not total:
        return allocation, amount
    remainders = []
    for work_id, capacity in capacities.items():
        allocation[work_id], remainder = divmod(distributable * capacity, total)
        remainders.append((work_id, remainder))
    pennies = distributable - sum(allocation.values())
    for work_id, _ in sorted(remainders, key=lambda item: (-item[1], item[0]))[:pennies]:
        allocation[work_id] += 1
    return allocation, amount - distributable


def _work_reports(works: list[Work], payments: list[Payment], as_of: date) -> tuple[list[WorkReport], int]:
    """Учитывает прямые платежи, разносит общие и формирует строки работ договора."""

    direct: dict[str, int] = defaultdict(int)
    general = 0
    for payment in payments:
        outgoing, refunded = payment_amounts_at(payment, as_of)
        if payment.work_id is None:
            general += outgoing - refunded
        else:
            direct[payment.work_id] += outgoing - refunded
    states = {work.work_id: work_status_at(work, as_of) for work in works}
    capacities = {
        work.work_id: max(0, work.price - direct[work.work_id])
        for work in works if states[work.work_id] != "cancelled"
    }
    allocated, advance = allocate_proportionally(general, capacities)
    rows = []
    for work in sorted(works, key=lambda item: item.work_id):
        obligation = 0 if states[work.work_id] == "cancelled" else work.price
        paid = direct[work.work_id] + allocated.get(work.work_id, 0)
        rows.append(WorkReport(
            work.contract_id, work.work_id, work.title, states[work.work_id], work.price,
            obligation, direct[work.work_id], allocated.get(work.work_id, 0), paid,
            max(0, obligation - paid), max(0, paid - obligation),
        ))
    return rows, advance


def _contract_report(contract: Contract, rows: list[WorkReport], payments: list[Payment],
                     advance: int, as_of: date) -> ContractReport:
    """Собирает итоги договора и проверяет равенство оплаты сумме её распределения."""

    movements = [payment_amounts_at(payment, as_of) for payment in payments]
    gross = sum(outgoing for outgoing, _ in movements)
    refunded = sum(returned for _, returned in movements)
    active = sum(row.obligation for row in rows)
    overpaid = sum(row.overpaid for row in rows)
    warnings = []
    if sum(row.price for row in rows) != contract.amount:
        warnings.append("Сумма цен работ отличается от суммы договора")
    if any(row.status == "cancelled" for row in rows):
        warnings.append("Отмена исключает цену работы из обязательств по работам, сумма договора сохранена")
    if overpaid:
        warnings.append("Есть прямые переплаты или платежи по отменённым работам; они не перераспределены")
    if advance:
        warnings.append("Есть нераспределённый аванс")
    assigned = sum(row.paid for row in rows)
    if assigned + advance != gross - refunded:
        raise RuntimeError(f"Money conservation failed for {contract.contract_id}")
    return ContractReport(
        contract.contract_id, contract.contractor, contract.amount,
        sum(row.price for row in rows), active,
        sum(row.price for row in rows if row.status == "done"),
        sum(row.price for row in rows if row.status == "cancelled"),
        gross, refunded, gross - refunded,
        sum(payment.amount for payment in payments if payment.created_at <= as_of
            and (payment.paid_at is None or payment.paid_at > as_of)),
        assigned, advance, sum(row.remaining for row in rows), overpaid,
        contract.amount - (gross - refunded), active - (gross - refunded), tuple(warnings),
    )


def build_report(data: Dataset, as_of: date) -> Report:
    """Строит отчёт из проверенной выгрузки с устойчивым порядком договоров и работ."""
    works_by_contract: dict[str, list[Work]] = defaultdict(list)
    payments_by_contract: dict[str, list[Payment]] = defaultdict(list)
    for work in data.works:
        works_by_contract[work.contract_id].append(work)
    for payment in data.payments:
        payments_by_contract[payment.contract_id].append(payment)
    work_rows = []
    contract_rows = []
    for contract in sorted(data.contracts, key=lambda item: item.contract_id):
        if contract.signed_at > as_of:
            continue
        payments = payments_by_contract[contract.contract_id]
        rows, advance = _work_reports(works_by_contract[contract.contract_id], payments, as_of)
        work_rows.extend(rows)
        contract_rows.append(_contract_report(contract, rows, payments, advance, as_of))
    return Report(as_of, tuple(work_rows), tuple(contract_rows))
