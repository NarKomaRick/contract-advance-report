"""Структуры исходных данных и результата отчёта.

Contract, Work и Payment описывают строки входных CSV, Dataset объединяет выгрузку.
WorkReport и ContractReport содержат рассчитанные строки, Report объединяет отчёт.
Все денежные поля хранят целые копейки. Чтения файлов и расчётов в этом модуле нет.
"""

from dataclasses import dataclass
from datetime import date


class InputError(ValueError):
    """Ошибка входных данных, при которой отчёт нельзя считать достоверным."""


@dataclass(frozen=True)
class Contract:
    """Договор: идентификатор, подрядчик, сумма в копейках и дата подписания."""

    contract_id: str
    contractor: str
    amount: int
    signed_at: date


@dataclass(frozen=True)
class Work:
    """Работа по договору с исходной ценой и датами приёмки или отмены."""

    work_id: str
    contract_id: str
    title: str
    price: int
    status: str
    completed_at: date | None
    cancelled_at: date | None


@dataclass(frozen=True)
class Payment:
    """Платёж с необязательной привязкой к работе и датами движения денег."""

    payment_id: str
    contract_id: str
    work_id: str | None
    purpose: str
    amount: int
    status: str
    created_at: date
    paid_at: date | None
    refunded_at: date | None


@dataclass(frozen=True)
class Dataset:
    """Полная проверенная выгрузка для передачи в расчётное ядро."""

    contracts: tuple[Contract, ...]
    works: tuple[Work, ...]
    payments: tuple[Payment, ...]


@dataclass(frozen=True)
class WorkReport:
    """Строка отчёта по работе: обязательство, оплата, остаток и переплата."""

    contract_id: str
    work_id: str
    title: str
    status: str
    price: int
    obligation: int
    direct_paid: int
    allocated_paid: int
    paid: int
    remaining: int
    overpaid: int


@dataclass(frozen=True)
class ContractReport:
    """Итоги договора, свободный аванс, два баланса и поясняющие примечания."""

    contract_id: str
    contractor: str
    contract_amount: int
    works_price: int
    active_works_price: int
    done_works_price: int
    cancelled_works_price: int
    gross_paid: int
    refunded: int
    net_paid: int
    queued: int
    assigned_paid: int
    unallocated_advance: int
    remaining_works: int
    overpaid_works: int
    contract_balance: int
    active_works_balance: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    """Готовый отчёт на одну дату, содержащий работы и итоги договоров."""

    as_of: date
    works: tuple[WorkReport, ...]
    contracts: tuple[ContractReport, ...]
