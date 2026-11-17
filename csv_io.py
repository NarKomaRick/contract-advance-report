"""Strict CSV input and accountant-facing CSV output."""

import csv
import re
import tempfile
from datetime import date
from pathlib import Path

from models import Contract, Dataset, InputError, Payment, Report, Work


def parse_money(value: str) -> int:
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value):
        raise ValueError(f"некорректная сумма {value!r}: нужны неотрицательные рубли и максимум 2 знака копеек")
    rubles, _, fraction = value.partition(".")
    return int(rubles) * 100 + int(fraction.ljust(2, "0"))


def format_money(value: int) -> str:
    sign = "-" if value < 0 else ""
    rubles, kopecks = divmod(abs(value), 100)
    return f"{sign}{rubles}.{kopecks:02d}"


def parse_date(value: str) -> date:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError(f"некорректная дата {value!r}: нужен формат YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"некорректная дата {value!r}: такого календарного дня нет") from error


def _optional_date(value: str) -> date | None:
    return parse_date(value) if value else None


def _records(path: Path, model: type, optional: set[str], money: str):
    """Yield records with their source line for precise reference errors."""
    expected = set(model.__dataclass_fields__)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        try:
            headers = reader.fieldnames or []
            if len(headers) != len(set(headers)) or set(headers) != expected:
                raise InputError(f"{path}: строка 1: ожидаются колонки {', '.join(sorted(expected))}")
            while True:
                line = reader.line_num + 1
                row = next(reader, None)
                if row is None:
                    break
                if None in row or any(value is None for value in row.values()):
                    raise InputError(f"{path}: строка {line}: число полей не совпадает с заголовком")
                values = {key: value.strip() for key, value in row.items()}
                try:
                    for key, value in values.items():
                        if not value and key not in optional:
                            raise ValueError(f"поле {key} обязательно")
                        if key.endswith("_at"):
                            values[key] = _optional_date(value)
                    values[money] = parse_money(values[money])
                    if "work_id" in optional:
                        values["work_id"] = values["work_id"] or None
                    yield line, model(**values)
                except ValueError as error:
                    raise InputError(f"{path}: строка {line}: {error}") from error
        except csv.Error as error:
            raise InputError(f"{path}: строка {reader.line_num}: некорректный CSV: {error}") from error


def _work_error(work: Work) -> str | None:
    if work.status not in {"planned", "done", "cancelled"}:
        return f"неизвестный статус работы {work.status!r}"
    if work.status == "planned" and (work.completed_at or work.cancelled_at):
        return "у planned не должно быть дат приёмки или отмены"
    if work.status == "done" and (work.completed_at is None or work.cancelled_at is not None):
        return "для done нужна completed_at и пустая cancelled_at"
    if work.status == "cancelled" and work.cancelled_at is None:
        return "для cancelled нужна cancelled_at"
    if work.completed_at and work.cancelled_at and work.completed_at > work.cancelled_at:
        return "приёмка не может быть позже отмены"
    return None


def _payment_error(payment: Payment) -> str | None:
    if payment.status not in {"queued", "paid", "refunded"}:
        return f"неизвестный статус платежа {payment.status!r}"
    if payment.status == "queued" and (payment.paid_at or payment.refunded_at):
        return "у queued не должно быть дат оплаты или возврата"
    if payment.status == "paid" and (payment.paid_at is None or payment.refunded_at is not None):
        return "для paid нужна paid_at и пустая refunded_at"
    if payment.status == "refunded" and (payment.paid_at is None or payment.refunded_at is None):
        return "для refunded нужны paid_at и refunded_at"
    if payment.paid_at and payment.paid_at < payment.created_at:
        return "оплата не может быть раньше создания платежа"
    if payment.refunded_at and payment.paid_at and payment.refunded_at < payment.paid_at:
        return "возврат не может быть раньше оплаты"
    return None


def load_dataset(directory: Path) -> Dataset:
    contracts: dict[str, Contract] = {}
    works: dict[str, Work] = {}
    payments: dict[str, Payment] = {}
    specs = (
        ("contracts.csv", Contract, set(), "amount", "contract_id", contracts),
        ("works.csv", Work, {"completed_at", "cancelled_at"}, "price", "work_id", works),
        ("payments.csv", Payment, {"work_id", "paid_at", "refunded_at"}, "amount", "payment_id", payments),
    )
    for filename, model, optional, money, id_field, destination in specs:
        path = directory / filename
        for line, record in _records(path, model, optional, money):
            problem = None
            if getattr(record, id_field) in destination:
                problem = f"повторный идентификатор {getattr(record, id_field)}"
            elif model is not Contract and record.contract_id not in contracts:
                problem = f"неизвестный договор {record.contract_id}"
            elif model is Work:
                problem = _work_error(record)
            elif model is Payment:
                problem = _payment_error(record)
                if record.work_id:
                    if record.work_id not in works:
                        problem = f"неизвестная работа {record.work_id}"
                    elif works[record.work_id].contract_id != record.contract_id:
                        problem = "работа платежа относится к другому договору"
            if problem:
                raise InputError(f"{path}: строка {line}: {problem}")
            destination[getattr(record, id_field)] = record
    return Dataset(tuple(contracts.values()), tuple(works.values()), tuple(payments.values()))


WORK_COLUMNS = (
    ("contract_id", "Договор"), ("work_id", "Работа"), ("title", "Название"),
    ("status", "Статус на дату"), ("price", "Цена, руб."),
    ("obligation", "Обязательство по работе, руб."), ("direct_paid", "Прямые платежи, руб."),
    ("allocated_paid", "Распределено общих платежей, руб."), ("paid", "Выплачено, руб."),
    ("remaining", "Осталось оплатить, руб."), ("overpaid", "Переплата, руб."),
)
CONTRACT_COLUMNS = (
    ("contract_id", "Договор"), ("contractor", "Подрядчик"),
    ("contract_amount", "Сумма договора, руб."), ("works_price", "Цена всех работ, руб."),
    ("active_works_price", "Цена неотменённых работ, руб."),
    ("done_works_price", "Цена принятых работ, руб."),
    ("cancelled_works_price", "Цена отменённых работ, руб."),
    ("gross_paid", "Перечислено, руб."), ("refunded", "Возвращено, руб."),
    ("net_paid", "Выплачено за вычетом возвратов, руб."), ("queued", "Ожидает перечисления, руб."),
    ("assigned_paid", "Отнесено на работы, руб."),
    ("unallocated_advance", "Нераспределённый аванс, руб."),
    ("remaining_works", "Осталось оплатить по работам, руб."),
    ("overpaid_works", "Переплаты по работам, руб."),
    ("contract_balance", "Баланс до суммы договора, руб."),
    ("active_works_balance", "Баланс до цены неотменённых работ, руб."),
    ("warnings", "Примечания"),
)
STATUS_LABELS = {"planned": "Запланирована", "done": "Принята", "cancelled": "Отменена"}


def _write_table(path: Path, columns: tuple, rows: tuple, as_of: date) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Дата отчёта", *(label for _, label in columns)])
        for row in rows:
            values = [as_of.isoformat()]
            for attribute, _ in columns:
                value = getattr(row, attribute)
                if isinstance(value, int):
                    value = format_money(value)
                elif attribute == "status":
                    value = STATUS_LABELS[value]
                elif attribute == "warnings":
                    value = "; ".join(value)
                values.append(value)
            writer.writerow(values)


def write_report(report: Report, directory: Path) -> None:
    """Publish both files together as a new directory; never replace existing data."""
    if directory.exists():
        raise FileExistsError(f"Папка результата уже существует: {directory}. Выберите другую --output")
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".report-", dir=directory.parent) as temporary:
        stage = Path(temporary) / "result"
        stage.mkdir()
        _write_table(stage / "works.csv", WORK_COLUMNS, report.works, report.as_of)
        _write_table(stage / "contracts.csv", CONTRACT_COLUMNS, report.contracts, report.as_of)
        stage.rename(directory)
