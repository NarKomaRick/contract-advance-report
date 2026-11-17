"""Проверки расчётов, загрузки и полного запуска CLI через pytest.

Проверяются деньги и округление, исторические даты, возвраты, отмены, переплаты,
ошибки CSV, независимость от порядка строк и защита готовых отчётов от перезаписи.
Фикстуры создают временные копии данных, исходная выгрузка не изменяется.
Запуск из корня проекта: python -m pytest -q.
"""

import csv
import os
import random
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from calculation import allocate_proportionally, build_report, payment_amounts_at, work_status_at
from csv_io import format_money, load_dataset, parse_money, write_report
from models import Contract, Dataset, InputError, Payment, Work


ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 11, 10)


@pytest.fixture
def data_dir(tmp_path):
    destination = tmp_path / "data"
    shutil.copytree(ROOT / "data", destination)
    return destination


@pytest.fixture
def sample_data(data_dir):
    return load_dataset(data_dir)


def rewrite_csv(path, change):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    change(rows)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.parametrize("text,expected", [("0", 0), ("0.01", 1), ("15.5", 1550),
                                          ("9007199254740993.99", 900719925474099399)])
def test_money_parsing_is_exact(text, expected):
    assert parse_money(text) == expected
    assert parse_money(format_money(expected)) == expected


@pytest.mark.parametrize("text", ["-1.00", "1.001", "1e2", "NaN", "inf", "1,20", "", "1.", ".50"])
def test_invalid_money_is_rejected(text):
    with pytest.raises(ValueError):
        parse_money(text)


def test_control_date_report_has_independently_checked_totals(sample_data):
    report = build_report(sample_data, AS_OF)
    contracts = {row.contract_id: row for row in report.contracts}
    works = {row.work_id: row for row in report.works}
    first, second = contracts["D-1"], contracts["D-2"]
    assert (first.gross_paid, first.refunded, first.net_paid, first.queued) == (
        36500000, 5000000, 31500000, 15000000,
    )
    assert (first.active_works_price, first.remaining_works, first.contract_balance) == (
        50000000, 18500000, 28500000,
    )
    assert first.done_works_price == 30000000
    assert (second.net_paid, second.done_works_price, second.remaining_works) == (7500000, 5000000, 1500000)
    assert works["W-13"].status == "cancelled"
    assert (works["W-13"].price, works["W-13"].paid, works["W-13"].remaining) == (10000000, 0, 0)
    assert works["W-23"].status == "planned"
    assert [works[key].paid for key in ("W-21", "W-22", "W-23")] == [1666667, 2500000, 3333333]
    assert works["W-14"].direct_paid == 1500000
    assert works["W-14"].paid == 6757732
    assert works["W-11"].paid == 6185567


@pytest.mark.parametrize("day,net", [("2026-09-01", 30000000), ("2026-09-02", 35000000),
                                    ("2026-09-09", 35000000), ("2026-09-10", 30000000)])
def test_current_refunded_status_does_not_erase_historical_payment(sample_data, day, net):
    report = build_report(sample_data, date.fromisoformat(day))
    assert report.contracts[0].net_paid == net


@pytest.mark.parametrize("day,status", [("2026-10-11", "planned"), ("2026-10-12", "cancelled")])
def test_cancellation_takes_effect_on_its_date(sample_data, day, status):
    work = next(work for work in sample_data.works if work.work_id == "W-13")
    assert work_status_at(work, date.fromisoformat(day)) == status


def test_completed_work_can_be_cancelled_later(sample_data):
    work = replace(sample_data.works[0], status="cancelled", cancelled_at=date(2026, 10, 12))
    assert work_status_at(work, date(2026, 9, 19)) == "planned"
    assert work_status_at(work, date(2026, 9, 20)) == "done"
    assert work_status_at(work, date(2026, 10, 12)) == "cancelled"


def test_future_payment_and_acceptance_enter_on_their_dates(sample_data):
    on_acceptance = build_report(sample_data, date(2026, 11, 11))
    assert on_acceptance.works[-1].status == "done"
    assert on_acceptance.contracts[-1].net_paid == 7500000
    on_payment = build_report(sample_data, date(2026, 11, 12))
    assert on_payment.contracts[-1].net_paid == 9500000
    assert on_payment.contracts[-1].unallocated_advance == 500000
    assert on_payment.contracts[-1].contract_balance == -500000
    assert on_payment.contracts[-1].remaining_works == 0


def test_before_signing_has_no_report_rows(sample_data):
    report = build_report(sample_data, date(2026, 8, 24))
    assert report.works == report.contracts == ()


def test_unpaid_payment_never_counts_as_cash(sample_data):
    queued = next(payment for payment in sample_data.payments if payment.status == "queued")
    assert payment_amounts_at(queued, date(2030, 1, 1)) == (0, 0)


def test_largest_remainders_have_stable_ties_and_no_overallocation():
    allocation, advance = allocate_proportionally(2, {"C": 1, "B": 1, "A": 1})
    assert allocation == {"A": 1, "B": 1, "C": 0}
    assert advance == 0
    assert allocate_proportionally(5, {"A": 1, "B": 2}) == ({"A": 1, "B": 2}, 2)
    assert allocate_proportionally(5, {"A": 0}) == ({"A": 0}, 5)
    assert allocate_proportionally(5, {}) == ({}, 5)


@pytest.mark.parametrize("amount,capacities", [(-1, {}), (1, {"A": -1})])
def test_negative_allocation_inputs_are_rejected(amount, capacities):
    with pytest.raises(ValueError):
        allocate_proportionally(amount, capacities)


def test_allocation_preserves_money_and_is_independent_of_input_order():
    generator = random.Random(20261110)
    for _ in range(200):
        capacities = {f"W-{i}": generator.randrange(100000) for i in range(generator.randrange(12))}
        amount = generator.randrange(1000000)
        allocated, advance = allocate_proportionally(amount, capacities)
        assert sum(allocated.values()) + advance == amount
        assert all(0 <= allocated[key] <= capacity for key, capacity in capacities.items())
        assert (allocated, advance) == allocate_proportionally(amount, dict(reversed(list(capacities.items()))))


def test_input_order_does_not_change_report(sample_data):
    reversed_data = Dataset(tuple(reversed(sample_data.contracts)), tuple(reversed(sample_data.works)),
                            tuple(reversed(sample_data.payments)))
    assert build_report(sample_data, AS_OF) == build_report(reversed_data, AS_OF)


def small_dataset(*, cancelled=False, direct=150, general=50):
    signed = date(2026, 1, 1)
    return Dataset(
        (Contract("D", "Подрядчик", 200, signed),),
        (Work("A", "D", "Первая", 100, "cancelled" if cancelled else "planned", None,
              signed if cancelled else None), Work("B", "D", "Вторая", 100, "planned", None, None)),
        (Payment("P1", "D", "A", "Прямая оплата", direct, "paid", signed, signed, None),
         Payment("P2", "D", None, "Аванс", general, "paid", signed, signed, None)),
    )


@pytest.mark.parametrize("cancelled", [False, True])
def test_direct_overpayment_stays_on_original_work(cancelled):
    report = build_report(small_dataset(cancelled=cancelled), AS_OF)
    first, second = report.works
    assert first.paid == 150
    assert first.overpaid == (150 if cancelled else 50)
    assert second.paid == 50
    assert second.remaining == 50
    assert report.contracts[0].net_paid == 200
    assert report.contracts[0].warnings


def test_refunded_direct_payment_releases_work_capacity():
    data = small_dataset(direct=100, general=100)
    refund = replace(data.payments[0], status="refunded", refunded_at=date(2026, 1, 2))
    report = build_report(replace(data, payments=(refund, data.payments[1])), AS_OF)
    assert [work.paid for work in report.works] == [50, 50]
    assert report.contracts[0].net_paid == 100


def test_contract_without_works_keeps_general_payment_as_advance():
    data = small_dataset(direct=0, general=100)
    report = build_report(replace(data, works=(), payments=(data.payments[1],)), AS_OF)
    assert report.works == ()
    assert report.contracts[0].unallocated_advance == 100
    assert report.contracts[0].warnings


@pytest.mark.parametrize("filename", ["contracts.csv", "works.csv", "payments.csv"])
def test_duplicate_ids_are_rejected_with_file_and_line(data_dir, filename):
    rewrite_csv(data_dir / filename, lambda rows: rows.append(rows[0]))
    with pytest.raises(InputError, match=filename + r".*строка.*повторный"):
        load_dataset(data_dir)


@pytest.mark.parametrize("filename,column,value,problem", [
    ("works.csv", "contract_id", "missing", "неизвестный договор"),
    ("payments.csv", "work_id", "missing", "неизвестная работа"),
    ("payments.csv", "work_id", "W-21", "другому договору"),
    ("payments.csv", "amount", "10.005", "некорректная сумма"),
    ("payments.csv", "paid_at", "2026-08-01", "раньше создания"),
    ("payments.csv", "status", "queued", "queued"),
    ("works.csv", "status", "unknown", "неизвестный статус"),
    ("works.csv", "completed_at", "", "поле completed_at|для done"),
    ("contracts.csv", "signed_at", "2026-02-30", "некорректная дата"),
])
def test_invalid_records_are_not_silently_skipped(data_dir, filename, column, value, problem):
    rewrite_csv(data_dir / filename, lambda rows: rows[0].update({column: value}))
    with pytest.raises(InputError, match=problem):
        load_dataset(data_dir)


def test_refund_before_payment_is_rejected(data_dir):
    rewrite_csv(data_dir / "payments.csv", lambda rows: rows[1].update(refunded_at="2026-09-01"))
    with pytest.raises(InputError, match="возврат не может"):
        load_dataset(data_dir)


@pytest.mark.parametrize("content", ["contract_id,contractor,amount\n",
                                     '"unclosed header\n',
                                     "contract_id,contractor,amount,signed_at,signed_at\n",
                                     "contract_id,contractor,amount,signed_at\nD,X,100\n",
                                     "contract_id,contractor,amount,signed_at\nD,X,100,2026-01-01,extra\n"])
def test_invalid_csv_structure_is_rejected(data_dir, content):
    (data_dir / "contracts.csv").write_text(content, encoding="utf-8")
    with pytest.raises(InputError):
        load_dataset(data_dir)


def test_csv_output_is_readable_and_reconciles(sample_data, tmp_path):
    report = build_report(sample_data, AS_OF)
    output = tmp_path / "report"
    write_report(report, output)
    with (output / "works.csv").open(encoding="utf-8-sig", newline="") as stream:
        works = list(csv.DictReader(stream))
    with (output / "contracts.csv").open(encoding="utf-8-sig", newline="") as stream:
        contracts = list(csv.DictReader(stream))
    assert len(works) == 9
    assert contracts[1]["Подрядчик"] == "Иван Петров, дизайнер"
    for contract in contracts:
        paid = sum(parse_money(work["Выплачено, руб."]) for work in works if work["Договор"] == contract["Договор"])
        assert paid + parse_money(contract["Нераспределённый аванс, руб."]) == parse_money(contract["Выплачено за вычетом возвратов, руб."])
    with pytest.raises(FileExistsError):
        write_report(report, output)


def run_cli(*arguments):
    return subprocess.run([sys.executable, str(ROOT / "report.py"), *map(str, arguments)],
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=ROOT)


def test_cli_success_and_output_protection(data_dir, tmp_path):
    output = tmp_path / "result"
    args = ("--data-dir", data_dir, "--date", "2026-11-10", "--output", output)
    result = run_cli(*args)
    assert result.returncode == 0, result.stderr
    assert "315000.00" in result.stdout
    original = (output / "contracts.csv").read_bytes()
    again = run_cli(*args)
    assert again.returncode == 1
    assert "уже существует" in again.stderr
    assert (output / "contracts.csv").read_bytes() == original


@pytest.mark.parametrize("day", ["10.11.2026", "2026-02-30", "20261110"])
def test_cli_bad_date_fails_without_creating_report(data_dir, tmp_path, day):
    output = tmp_path / "result"
    result = run_cli("--data-dir", data_dir, "--date", day, "--output", output)
    assert result.returncode == 1
    assert "Ошибка" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


def test_cli_bad_input_does_not_create_partial_report(data_dir, tmp_path):
    (data_dir / "payments.csv").write_text("wrong\n", encoding="utf-8")
    output = tmp_path / "result"
    result = run_cli("--data-dir", data_dir, "--date", "2026-11-10", "--output", output)
    assert result.returncode == 1
    assert "payments.csv" in result.stderr
    assert not output.exists()


def test_cli_help_and_required_date():
    assert run_cli("--help").returncode == 0
    assert run_cli().returncode == 2
