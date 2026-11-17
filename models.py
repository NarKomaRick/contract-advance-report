"""Typed input and report records. All monetary fields are integer kopecks."""

from dataclasses import dataclass
from datetime import date


class InputError(ValueError):
    """An input file cannot be interpreted safely."""


@dataclass(frozen=True)
class Contract:
    contract_id: str
    contractor: str
    amount: int
    signed_at: date


@dataclass(frozen=True)
class Work:
    work_id: str
    contract_id: str
    title: str
    price: int
    status: str
    completed_at: date | None
    cancelled_at: date | None


@dataclass(frozen=True)
class Payment:
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
    contracts: tuple[Contract, ...]
    works: tuple[Work, ...]
    payments: tuple[Payment, ...]


@dataclass(frozen=True)
class WorkReport:
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
    as_of: date
    works: tuple[WorkReport, ...]
    contracts: tuple[ContractReport, ...]
