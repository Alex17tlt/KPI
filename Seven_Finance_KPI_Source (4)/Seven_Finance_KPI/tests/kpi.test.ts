import test from "node:test";
import assert from "node:assert/strict";
import {
  defaultPolicy, generateTasks, calculateMonth, taskStatus, dateAtTime,
  moscowDate, moscowTime, analysisDeadline, previousWorkingDate, validatePolicy,
} from "../lib/kpi.ts";
import type { BlockId, Issue, MonthRecord, Task } from "../lib/kpi.ts";

const END = "2026-12-03T12:00:00.000Z";

function successful(month = "2026-11", startDate = `${month}-01`): MonthRecord {
  const policy = defaultPolicy("notion5", startDate);
  const record: MonthRecord = { month, policy, submissions: {}, issues: [], revenuePercent: 97, revenueEvidence: "Реестр ДЗ, сверка закрытого месяца", closure: null, revision: 0 };
  for (const task of generateTasks(month, policy)) {
    record.submissions[task.id] = { taskId: task.id, at: task.deadlineAt, checks: Object.fromEntries(task.checks.map(check => [check.key, true])), comment: "", by: "Даниил" };
  }
  return record;
}

function block(record: MonthRecord, id: BlockId, now = END) {
  return calculateMonth(record, now).blocks.find(item => item.id === id)!;
}

function tasksOf(record: MonthRecord, predicate: (task: Task) => boolean): Task[] {
  return generateTasks(record.month, record.policy).filter(predicate);
}

function fail(record: MonthRecord, task: Task, key = task.checks[0].key): void {
  record.submissions[task.id].checks[key] = false;
}

function issue(overrides: Partial<Issue> = {}): Issue {
  return {
    id: "issue-1", date: "2026-11-13", kind: "error", severity: "minor", category: "spend", description: "Расхождение в спенде", foundBy: "HR", foundByDaniel: false,
    correctedBeforeSending: false, repeat: false, analysis: "Причина найдена; контроль добавлен", analysisAt: "2026-11-16T12:00:00.000Z", createdAt: "2026-11-13T12:00:00.000Z", ...overrides,
  };
}

test("идеальный завершённый месяц: 2 000 KPI плюс 2 500 фикс", () => {
  const record = successful();
  record.closure = { at: END, by: "HR", note: "Все записи проверены" };
  const result = calculateMonth(record, END);
  assert.deepEqual(result.blocks.map(item => item.earned), [600, 500, 400, 300, 200]);
  assert.equal(result.bonus, 2000);
  assert.equal(result.total, 4500);
  assert.equal(result.provisional, false);
});

test("границы ежедневного учёта 0, 1, 3 и 4 нарушения", () => {
  for (const [count, expected] of [[0, 600], [1, 300], [3, 300], [4, 0]]) {
    const record = successful();
    const candidates = tasksOf(record, task => task.block === 1);
    candidates.slice(0, count).forEach(task => fail(record, task));
    assert.equal(block(record, 1).earned, expected, `${count} нарушений`);
  }
});

test("границы сверок и выплат 0, 1, 2 и 3 нарушения", () => {
  for (const [count, expected] of [[0, 500], [1, 250], [2, 250], [3, 0]]) {
    const record = successful();
    tasksOf(record, task => task.block === 2 && task.kind !== "payment").slice(0, count).forEach(task => fail(record, task));
    assert.equal(block(record, 2).earned, expected);
  }
});

test("границы контроля расходов и планирования", () => {
  for (const [count, expected] of [[0, 300], [1, 150], [3, 150], [4, 0]]) {
    const record = successful();
    tasksOf(record, task => task.kind === "morning").slice(0, count).forEach(task => fail(record, task, "cards"));
    assert.equal(block(record, 4).earned, expected);
  }
  for (const [count, expected] of [[0, 200], [1, 100], [2, 0]]) {
    const record = successful();
    tasksOf(record, task => task.kind === "plan").slice(0, count).forEach(task => fail(record, task));
    assert.equal(block(record, 5).earned, expected);
  }
});

test("выручка: точные границы 95 и 90 процентов вместе с алертами", () => {
  for (const [percent, alerts, expected] of [[95, 0, 400], [94.999, 0, 200], [90, 1, 200], [89.999, 0, 0], [100, 1, 200], [100, 2, 0], [0, 0, 0]]) {
    const record = successful();
    record.revenuePercent = percent;
    record.issues = Array.from({ length: alerts }, (_, i) => issue({ id: `alert-${i}`, kind: "alert", analysis: "", analysisAt: null }));
    assert.equal(block(record, 3).earned, expected, `${percent}% и ${alerts} алертов`);
    assert.equal(calculateMonth(record, END).penaltiesRaw, 0, "алерты не дают штраф и надбавку за разбор");
  }
});

test("неизвестная выручка отличается от нулевой и требует источника", () => {
  const record = successful();
  record.revenuePercent = null;
  assert.equal(block(record, 3).earned, 0);
  assert.equal(block(record, 3).potential, 400);
  assert.equal(calculateMonth(record, END).missingRevenue, true);
  record.revenuePercent = 0;
  assert.equal(block(record, 3).potential, 0);
  assert.equal(calculateMonth(record, END).missingRevenue, false);
  record.revenuePercent = 97;
  record.revenueEvidence = "  ";
  assert.equal(calculateMonth(record, END).missingRevenue, true);
  assert.equal(block(record, 3).earned, 0);
});

test("одно опоздание выплаты байерам обнуляет весь блок 2", () => {
  const record = successful();
  const task = tasksOf(record, item => !!item.buyerPayment)[0];
  assert.equal(block(record, 2).earned, 500);
  record.submissions[task.id].at = new Date(Date.parse(task.deadlineAt) + 1).toISOString();
  assert.equal(block(record, 2).earned, 0);
  assert.equal(calculateMonth(record, END).buyerPaymentLate, true);
  delete record.submissions[task.id];
  assert.equal(block(record, 2).earned, 0, "отсутствующая выплата после срока тоже просрочена");
});

test("ошибка выплаты без опоздания даёт одно нарушение, но не автоматическое обнуление", () => {
  const record = successful();
  const task = tasksOf(record, item => !!item.buyerPayment)[0];
  fail(record, task);
  assert.equal(block(record, 2).earned, 250);
  assert.equal(calculateMonth(record, END).buyerPaymentLate, false);
});

test("опоздание и несколько плохих проверок одной сдачи считаются один раз", () => {
  const record = successful();
  const task = tasksOf(record, item => item.kind === "morning")[0];
  const submission = record.submissions[task.id];
  submission.at = new Date(Date.parse(task.deadlineAt) + 3_600_000).toISOString();
  submission.checks = Object.fromEntries(task.checks.map(check => [check.key, false]));
  assert.equal(block(record, 1).violations, 1);
  assert.equal(block(record, 4).violations, 1);
});

test("утренняя просрочка с корректными картами не даёт нарушения блока 4", () => {
  const record = successful();
  const task = tasksOf(record, item => item.kind === "morning")[0];
  record.submissions[task.id].at = new Date(Date.parse(task.deadlineAt) + 60_000).toISOString();
  assert.equal(block(record, 1).violations, 1);
  assert.equal(block(record, 4).violations, 0);
  delete record.submissions[task.id];
  assert.equal(block(record, 4).violations, 1);
});

test("будущие задачи не штрафуются, пустой месяц не выглядит заработанным KPI", () => {
  const record = successful();
  record.submissions = {};
  record.revenuePercent = null;
  record.revenueEvidence = "";
  const now = "2026-11-01T06:00:00.000Z";
  const result = calculateMonth(record, now);
  assert.equal(result.overdueCount, 0);
  assert.equal(result.buyerPaymentLate, false);
  assert.equal(result.penaltiesRaw, 0);
  assert.equal(result.bonus, 0);
  assert.equal(result.potentialBonus, 2000);
  assert.equal(result.provisional, true);
  assert.ok(result.blocks.every(item => item.earned === 0));
});

test("дата старта исключает прошлые задачи и замечания", () => {
  const record = successful("2026-11", "2026-11-16");
  const tasks = generateTasks(record.month, record.policy);
  assert.ok(tasks.every(task => task.date >= "2026-11-16"));
  record.issues = [issue({ date: "2026-11-13" })];
  assert.equal(calculateMonth(record, END).penaltiesRaw, 0);
  record.policy.startDate = "2026-12-01";
  assert.equal(generateTasks(record.month, record.policy).length, 0);
  assert.equal(calculateMonth(record, END).potentialBonus, 0);
  assert.equal(calculateMonth(record, END).provisional, true);
});

test("выходной срок переносится на пятницу, февраль использует последний день", () => {
  const policy = defaultPolicy("notion5", "2026-01-01");
  const february = generateTasks("2026-02", policy);
  const payment = february.find(task => task.id === "monthly:2026-02:infra-pay-2")!;
  assert.equal(payment.date, "2026-02-27");
  assert.equal(payment.time, "23:59");
  const june = generateTasks("2026-06", policy);
  assert.equal(june.find(task => task.id === "monthly:2026-06:buyers-pay-2")!.date, "2026-06-19");
});

test("перенос из следующего месяца не теряет и не дублирует задачу", () => {
  const policy = defaultPolicy("notion5", "2025-01-01");
  const january = generateTasks("2025-01", policy);
  const february = generateTasks("2025-02", policy);
  const id = "monthly:2025-02:advertiser";
  assert.equal(january.filter(task => task.id === id).length, 1);
  assert.equal(january.find(task => task.id === id)!.date, "2025-01-31");
  assert.equal(february.filter(task => task.id === id).length, 0);
  assert.equal(new Set([...january, ...february].map(task => task.id)).size, january.length + february.length);
});

test("ручной календарь переносит срок и сохраняет недельную задачу", () => {
  const policy = defaultPolicy("notion5", "2026-01-01");
  policy.holidays = ["2026-11-02"];
  assert.equal(previousWorkingDate("2026-11-02", policy), "2026-10-30");
  const october = generateTasks("2026-10", policy);
  assert.equal(october.find(task => task.id === "weekly:2026-11-02:plan")!.date, "2026-10-30");
  policy.workingDates = ["2026-11-01"];
  assert.equal(previousWorkingDate("2026-11-02", policy), "2026-11-01");
});

test("штрафы 50/200/500, повтор ×2 и защита фиксированной части", () => {
  const record = successful();
  record.issues = [issue(), issue({ id: "2", severity: "significant", repeat: true }), issue({ id: "3", severity: "critical", repeat: true }), issue({ id: "4", severity: "critical", repeat: true })];
  const result = calculateMonth(record, END);
  assert.equal(result.penaltiesRaw, 2450);
  assert.equal(result.penaltiesApplied, 2000);
  assert.equal(result.bonus, 0);
  assert.equal(result.total, 2500);
  assert.equal(result.penaltyRows[1].multiplier, 2);
});

test("ошибка Даниила без штрафа; отменённые записи и дубликаты не штрафуются", () => {
  const record = successful();
  const normal = issue();
  record.issues = [normal, normal, issue({ id: "own", foundByDaniel: true, analysis: "", analysisAt: null }), issue({ id: "voided", severity: "critical", voided: true }), issue({ id: "voided-alert", kind: "alert", voided: true })];
  const result = calculateMonth(record, END);
  assert.equal(result.penaltiesRaw, 50);
  assert.equal(result.penaltyRows.length, 2);
  assert.equal(block(record, 3).violations, 0);
});

test("разбор за два рабочих дня учитывает выходные и точную границу", () => {
  const record = successful();
  const item = issue({ analysis: "", analysisAt: null });
  record.issues = [item];
  assert.equal(analysisDeadline(item, record.policy), "2026-11-17T20:59:59.999Z");
  assert.equal(calculateMonth(record, "2026-11-17T20:59:59.999Z").penaltiesRaw, 50);
  assert.equal(calculateMonth(record, "2026-11-17T21:00:00.000Z").penaltiesRaw, 100);
  item.analysis = "Причина и новая проверка";
  item.analysisAt = "2026-11-17T20:59:59.999Z";
  assert.equal(calculateMonth(record, END).penaltiesRaw, 50);
  item.analysisAt = "2026-11-17T21:00:00.000Z";
  assert.equal(calculateMonth(record, END).penaltiesRaw, 100);
});

test("подсказка повтора смотрит три календарных месяца, но штраф требует подтверждения", () => {
  const record = successful();
  record.issues = [issue({ date: "2026-11-16", repeat: false })];
  const history = [issue({ id: "old", date: "2026-08-16", createdAt: "2026-08-16T10:00:00Z" })];
  const result = calculateMonth(record, END, history);
  assert.equal(result.penaltyRows[0].repeatSuggested, true);
  assert.equal(result.penaltyRows[0].multiplier, 1);
  history[0].date = "2026-08-15";
  assert.equal(calculateMonth(record, END, history).penaltyRows[0].repeatSuggested, false);
  history[0].date = "2026-08-16";
  history[0].voided = true;
  assert.equal(calculateMonth(record, END, history).penaltyRows[0].repeatSuggested, false);
});

test("закрытие замораживает расчёт на момент проверки", () => {
  const record = successful();
  const item = issue({ date: "2026-11-30", createdAt: "2026-11-30T12:00:00Z", analysis: "", analysisAt: null });
  record.issues = [item];
  record.closure = { at: "2026-12-01T12:00:00Z", by: "HR", note: "Закрыто" };
  const result = calculateMonth(record, "2026-12-20T12:00:00Z");
  assert.equal(result.evaluatedAt, record.closure.at);
  assert.equal(result.penaltiesRaw, 50);
  record.closure = null;
  assert.equal(calculateMonth(record, "2026-12-20T12:00:00Z").penaltiesRaw, 100);
});

test("пример рабочего месяца из Notion воспроизводится: KPI 1 500, итого 4 000", () => {
  const record = successful();
  const mornings = tasksOf(record, task => task.kind === "morning");
  fail(record, mornings[0], "balance");
  fail(record, mornings[0], "cards");
  fail(record, mornings[1], "balance");
  record.revenuePercent = 96;
  record.issues = [issue()];
  const result = calculateMonth(record, END);
  assert.equal(result.bonus, 1500);
  assert.equal(result.total, 4000);
});

test("пример проблемного месяца: начислено 450, штрафы 700, KPI 0", () => {
  const record = successful();
  const mornings = tasksOf(record, task => task.kind === "morning");
  mornings.slice(0, 5).forEach(task => fail(record, task, "balance"));
  mornings.slice(0, 2).forEach(task => fail(record, task, "cards"));
  const pay = tasksOf(record, task => !!task.buyerPayment)[1];
  record.submissions[pay.id].at = new Date(Date.parse(pay.deadlineAt) + 86_400_000).toISOString();
  fail(record, tasksOf(record, task => task.kind === "plan")[0]);
  record.revenuePercent = 91;
  record.issues = [issue({ id: "alert", kind: "alert" }), issue({ id: "critical", severity: "critical" }), issue({ id: "significant", severity: "significant" })];
  const result = calculateMonth(record, END);
  assert.equal(result.gross, 450);
  assert.equal(result.penaltiesRaw, 700);
  assert.equal(result.penaltiesApplied, 450);
  assert.equal(result.bonus, 0);
  assert.equal(result.total, 2500);
});

test("редакция Олега остаётся черновиком: четыре блока и без штрафа 200 за закрытие", () => {
  const policy = defaultPolicy("oleg4", "2026-11-01");
  const record: MonthRecord = { month: "2026-11", policy, submissions: {}, issues: [], revenuePercent: null, revenueEvidence: "", closure: null, revision: 0 };
  const tasks = generateTasks(record.month, policy);
  assert.equal(tasks.find(task => task.kind === "morning")!.time, "15:00");
  assert.ok(tasks.some(task => task.kind === "dz"));
  assert.ok(tasks.some(task => task.kind === "kz"));
  assert.ok(tasks.some(task => task.kind === "closing" && task.detail.includes("2026-09")));
  assert.ok(!tasks.some(task => task.kind === "evening"));
  const result = calculateMonth(record, END);
  assert.deepEqual(result.blocks.map(item => item.id), [1, 2, 4, 5]);
  assert.equal(result.missingRevenue, false);
  assert.equal(result.penaltiesRaw, 0);
  assert.equal(result.provisional, true);
});

test("календарь привязан к Москве, а не часовому поясу сервера", () => {
  assert.equal(moscowDate("2026-11-15T21:30:00Z"), "2026-11-16");
  assert.equal(moscowTime("2026-11-15T21:30:00Z"), "00:30");
  assert.equal(dateAtTime("2026-11-16", "12:00"), "2026-11-16T09:00:00.000Z");
});

test("неполная или будущая запись не подтверждает сдачу", () => {
  const record = successful();
  const task = tasksOf(record, task => task.kind === "morning")[0];
  const submission = record.submissions[task.id];
  delete submission.checks.cards;
  assert.equal(taskStatus(task, submission, new Date(Date.parse(task.deadlineAt) + 1).toISOString()), "violation");
  submission.checks.cards = true;
  assert.equal(taskStatus(task, submission, new Date(Date.parse(task.deadlineAt) - 1).toISOString()), "pending");
});

test("невалидные проценты и лимит бонуса не принимаются", () => {
  const record = successful();
  record.revenuePercent = -1;
  assert.throws(() => calculateMonth(record, END), /Доля выручки/);
  record.revenuePercent = 101;
  assert.throws(() => calculateMonth(record, END), /Доля выручки/);
  record.revenuePercent = NaN;
  assert.throws(() => calculateMonth(record, END), /Доля выручки/);
  record.policy.amounts[1] = 601;
  assert.throws(() => validatePolicy(record.policy), /2 000/);
});
