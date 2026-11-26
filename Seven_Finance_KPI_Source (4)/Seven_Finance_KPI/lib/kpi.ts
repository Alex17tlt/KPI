/**
 * KPI Head of Finance. Calculation rules are separate from the UI and storage.
 * notion5 reproduces the supplied Notion; oleg4 is an explicitly marked draft.
 * All calendar deadlines use Europe/Moscow. Public holidays are configured,
 * never inferred from the visitor's browser or from a country calendar.
 */
export type BlockId = 1 | 2 | 3 | 4 | 5;
export type Profile = "notion5" | "oleg4";
export type TaskKind = "morning" | "evening" | "report" | "payment" | "plan" | "card" | "dz" | "kz" | "closing";
export type TaskState = "pending" | "overdue" | "done" | "violation";

export interface Policy {
  profile: Profile;
  fixed: number;
  amounts: Record<BlockId, number>;
  morningTime: string;
  eveningTime: string;
  dzTime: string;
  kzTime: string;
  paymentTime: string;
  deadlineTime: string;
  preparationTime: string;
  payrollDay: number | null;
  holidays: string[];
  workingDates: string[];
  startDate: string;
  draft: boolean;
}

export interface Task {
  id: string;
  date: string;
  time: string;
  deadlineAt: string;
  title: string;
  detail: string;
  block: BlockId;
  kind: TaskKind;
  buyerPayment?: boolean;
  checks: { key: string; label: string }[];
}

export interface Submission {
  taskId: string;
  at: string;
  checks: Record<string, boolean>;
  comment: string;
  by: string;
}

export interface Issue {
  id: string;
  date: string;
  kind: "error" | "alert";
  severity: "minor" | "significant" | "critical";
  category: string;
  description: string;
  foundBy: string;
  foundByDaniel: boolean;
  correctedBeforeSending: boolean;
  repeat: boolean;
  analysis: string;
  analysisAt: string | null;
  createdAt: string;
  closed?: boolean;
  voided?: boolean;
}

export interface MonthRecord {
  month: string;
  policy: Policy;
  submissions: Record<string, Submission>;
  issues: Issue[];
  revenuePercent: number | null;
  revenueEvidence: string;
  closure: null | { at: string; by: string; note: string };
  revision: number;
}

export interface BlockResult {
  id: BlockId;
  name: string;
  max: number;
  violations: number;
  coefficient: number;
  earned: number;
  potential: number;
  pending: number;
  reason: string;
}

export interface PenaltyRow {
  id: string;
  base: number;
  multiplier: number;
  analysisLate: boolean;
  analysisFee: number;
  total: number;
  exempt: boolean;
  reason: string;
  analysisDue: string;
  repeatSuggested: boolean;
}

export interface MonthCalculation {
  blocks: BlockResult[];
  penaltiesRaw: number;
  penaltiesApplied: number;
  potentialPenaltiesApplied: number;
  bonus: number;
  potentialBonus: number;
  total: number;
  potentialTotal: number;
  gross: number;
  potentialGross: number;
  missingRevenue: boolean;
  overdueCount: number;
  completedCount: number;
  totalTasks: number;
  provisional: boolean;
  periodEnded: boolean;
  buyerPaymentLate: boolean;
  penaltyRows: PenaltyRow[];
  taskResults: { task: Task; submission?: Submission; status: TaskState }[];
  warnings: string[];
  evaluatedAt: string;
}

const BLOCK_NAMES: Record<BlockId, string> = {
  1: "Ежедневный учёт",
  2: "Сверки и выплаты",
  3: "Приходы и алерты",
  4: "Контроль расходов",
  5: "Планирование на 45 дней",
};
const MOSCOW = "Europe/Moscow";
const MONTH = /^\d{4}-\d{2}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TIME = /^(?:[01]\d|2[0-3]):[0-5]\d$/;

export function moscowDate(iso: string | Date = new Date()): string {
  const date = typeof iso === "string" ? new Date(iso) : iso;
  if (!Number.isFinite(date.getTime())) throw new Error("Некорректная дата расчёта");
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: MOSCOW, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
  return `${parts.find(p => p.type === "year")!.value}-${parts.find(p => p.type === "month")!.value}-${parts.find(p => p.type === "day")!.value}`;
}

export function moscowTime(iso: string | Date = new Date()): string {
  const date = typeof iso === "string" ? new Date(iso) : iso;
  if (!Number.isFinite(date.getTime())) throw new Error("Некорректное время расчёта");
  return new Intl.DateTimeFormat("en-GB", { timeZone: MOSCOW, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
}

export function dateAtTime(date: string, time: string): string {
  assertDate(date);
  if (!TIME.test(time)) throw new Error("Время должно быть в формате ЧЧ:ММ");
  return new Date(`${date}T${time}:00+03:00`).toISOString();
}

function assertDate(date: string): void {
  if (!DATE.test(date) || new Date(`${date}T12:00:00Z`).toISOString().slice(0, 10) !== date) throw new Error("Некорректная календарная дата");
}

function assertMonth(month: string): void {
  if (!MONTH.test(month) || Number(month.slice(5)) < 1 || Number(month.slice(5)) > 12) throw new Error("Месяц должен быть в формате ГГГГ-ММ");
}

export function addDays(date: string, days: number): string {
  assertDate(date);
  const result = new Date(`${date}T12:00:00Z`);
  result.setUTCDate(result.getUTCDate() + days);
  return result.toISOString().slice(0, 10);
}

export function shiftMonth(month: string, delta: number): string {
  assertMonth(month);
  const date = new Date(`${month}-01T12:00:00Z`);
  date.setUTCMonth(date.getUTCMonth() + delta);
  return date.toISOString().slice(0, 7);
}

function lastDate(month: string): string {
  return addDays(`${shiftMonth(month, 1)}-01`, -1);
}

function nominalDate(month: string, day: number): string {
  return `${month}-${String(Math.min(day, Number(lastDate(month).slice(-2)))).padStart(2, "0")}`;
}

export function isWorkingDate(date: string, policy: Pick<Policy, "holidays" | "workingDates">): boolean {
  assertDate(date);
  if (policy.workingDates.includes(date)) return true;
  if (policy.holidays.includes(date)) return false;
  const weekday = new Date(`${date}T12:00:00Z`).getUTCDay();
  return weekday !== 0 && weekday !== 6;
}

export function previousWorkingDate(date: string, policy: Pick<Policy, "holidays" | "workingDates">): string {
  let result = date;
  for (let i = 0; i < 370; i++) {
    if (isWorkingDate(result, policy)) return result;
    result = addDays(result, -1);
  }
  throw new Error("В календаре не найден рабочий день");
}

export function addWorkingDays(date: string, count: number, policy: Pick<Policy, "holidays" | "workingDates">): string {
  let result = date;
  let remaining = count;
  for (let i = 0; remaining > 0 && i < 740; i++) {
    result = addDays(result, 1);
    if (isWorkingDate(result, policy)) remaining--;
  }
  if (remaining) throw new Error("В календаре недостаточно рабочих дней");
  return result;
}

function seventhWorkingDate(month: string, policy: Policy): string {
  let count = 0;
  for (let date = `${month}-01`; date <= lastDate(month); date = addDays(date, 1)) {
    if (isWorkingDate(date, policy) && ++count === 7) return date;
  }
  throw new Error("В месяце меньше семи рабочих дней");
}

export function defaultPolicy(profile: Profile = "notion5", startDate = moscowDate()): Policy {
  assertDate(startDate);
  return {
    profile,
    fixed: 2500,
    amounts: profile === "oleg4" ? { 1: 800, 2: 700, 3: 0, 4: 300, 5: 200 } : { 1: 600, 2: 500, 3: 400, 4: 300, 5: 200 },
    morningTime: profile === "oleg4" ? "15:00" : "12:00",
    eveningTime: "20:00",
    dzTime: "15:00",
    kzTime: "15:00",
    paymentTime: profile === "oleg4" ? "16:00" : "23:59",
    deadlineTime: "23:59",
    preparationTime: profile === "oleg4" ? "15:00" : "23:59",
    payrollDay: null,
    holidays: [],
    workingDates: [],
    startDate,
    draft: profile === "oleg4",
  };
}

export function validatePolicy(policy: Policy): void {
  if (!policy || !["notion5", "oleg4"].includes(policy.profile)) throw new Error("Неизвестная редакция правил");
  assertDate(policy.startDate);
  for (const key of ["morningTime", "eveningTime", "dzTime", "kzTime", "paymentTime", "deadlineTime", "preparationTime"] as const) {
    if (!TIME.test(policy[key])) throw new Error(`Некорректное время: ${key}`);
  }
  const amounts = ([1, 2, 3, 4, 5] as BlockId[]).map(id => policy.amounts[id]);
  if (amounts.some(amount => !Number.isFinite(amount) || amount < 0) || amounts.reduce((sum, amount) => sum + amount, 0) > 2000.000001) throw new Error("Суммы блоков должны быть неотрицательными и в сумме не больше 2 000 $");
  if (!Number.isFinite(policy.fixed) || policy.fixed < 0) throw new Error("Фикс должен быть неотрицательным числом");
  if (policy.profile === "oleg4" && policy.amounts[3] !== 0) throw new Error("В редакции Олега блок получения денег отключён");
  if (policy.payrollDay !== null && (!Number.isInteger(policy.payrollDay) || policy.payrollDay < 1 || policy.payrollDay > 31)) throw new Error("День расчёта ЗП должен быть от 1 до 31");
  policy.holidays.forEach(assertDate);
  policy.workingDates.forEach(assertDate);
}

/** Tasks belong to the month of their actual, shifted deadline. */
export function generateTasks(month: string, policy: Policy): Task[] {
  assertMonth(month);
  validatePolicy(policy);
  const tasks: Task[] = [];
  const add = (task: Omit<Task, "deadlineAt">): void => {
    if (task.date.slice(0, 7) === month && task.date >= policy.startDate) tasks.push({ ...task, deadlineAt: dateAtTime(task.date, task.time) });
  };
  const correct = [{ key: "correct", label: "Данные сверены, расхождения устранены" }];
  const payment = [{ key: "correct", label: "Выплата проведена без ошибок" }];

  for (let date = `${month}-01`; date <= lastDate(month); date = addDays(date, 1)) {
    if (isWorkingDate(date, policy)) {
      const monday = new Date(`${date}T12:00:00Z`).getUTCDay() === 1;
      if (policy.profile === "notion5") {
        add({ id: `daily:${date}:morning`, date, time: policy.morningTime, title: "Реестр ДС и ДДС за предыдущий день", detail: monday ? "Охват: пятница и выходные. Все счета, карты, кошельки и наличные в ₽ и $." : "Остатки и операции за предыдущий день, включая ₽ и $.", block: 1, kind: "morning", checks: [
          { key: "balance", label: "Остатки сходятся с фактом" },
          { key: "allAccounts", label: "Все счета, карты, кошельки и наличные учтены" },
          { key: "cards", label: "Списания разнесены; по непонятным задан вопрос" },
        ] });
        add({ id: `daily:${date}:evening`, date, time: policy.eveningTime, title: "Спенды по аккаунтам и байерам за сегодня", detail: "Каждый спенд привязан к правильному аккаунту, байеру и ссылке.", block: 1, kind: "evening", checks: [
          { key: "spend", label: "Спенды собраны и разнесены верно" },
          { key: "warmups", label: "Прогревы и новые аккаунты учтены" },
        ] });
      } else {
        add({ id: `daily:${date}:dds`, date, time: policy.morningTime, title: "Ежедневный отчёт по ДДС", detail: "Единый отчёт: остатки, поступления, платежи, карты, спенды и прогревы. Редакция из поздних правок.", block: 1, kind: "morning", checks: [
          { key: "balance", label: "Остатки сходятся с фактом" },
          { key: "categorized", label: "Поступления и платежи разнесены, комментарии заполнены" },
          { key: "cards", label: "Списания с карт разнесены по командам" },
          { key: "spend", label: "Спенды и прогревы учтены" },
        ] });
        add({ id: `daily:${date}:dz`, date, time: policy.dzTime, title: "Сверка ДЗ за предыдущий день", detail: "Сверить ожидаемые и фактические поступления; оповестить о просрочках. Время 15:00 требует подтверждения.", block: 1, kind: "dz", checks: [
          { key: "correct", label: "Ожидаемые и фактические поступления сверены" },
          { key: "alerts", label: "По просрочкам направлены оповещения" },
        ] });
        add({ id: `daily:${date}:kz`, date, time: policy.kzTime, title: "Сверка КЗ за предыдущий день", detail: "Обязательства, сроки и фактические платежи сверены. Время 15:00 требует подтверждения.", block: 1, kind: "kz", checks: correct });
      }
    }
  }
  for (let nominalMonday = `${month}-01`; nominalMonday <= `${shiftMonth(month, 1)}-07`; nominalMonday = addDays(nominalMonday, 1)) {
    if (new Date(`${nominalMonday}T12:00:00Z`).getUTCDay() === 1) {
      const date = previousWorkingDate(nominalMonday, policy);
      add({ id: `weekly:${nominalMonday}:balances`, date, time: policy.deadlineTime, title: "Сводка ДС, ДЗ и КЗ за неделю", detail: "ДС — фактические остатки денежных средств на счетах, картах, кошельках и в кассе на дату отчёта. Ожидаемые поступления и дебиторская задолженность показываются отдельно и не прибавляются к остаткам ДС. ДЗ и КЗ актуальны, суммы и сроки сверены. ДДС показывает поступления и выплаты за период.", block: 2, kind: "report", checks: correct });
      add({ id: `weekly:${nominalMonday}:plan`, date, time: policy.deadlineTime, title: "Платёжный календарь на 45 дней", detail: "Все известные приходы и платежи, а также даты возможной нехватки денег.", block: 5, kind: "plan", checks: [
        { key: "updated", label: "Календарь обновлён" },
        { key: "horizon", label: "Горизонт не меньше 45 дней, все известные потоки учтены" },
      ] });
    }
  }

  // Include following-month events shifted back into this month.
  for (const nominalMonth of [month, shiftMonth(month, 1)]) {
    const monthly = (key: string, day: number, title: string, block: BlockId, kind: TaskKind, detail: string, checks = correct, time = policy.deadlineTime, buyerPayment = false) => {
      add({ id: `monthly:${nominalMonth}:${key}`, date: previousWorkingDate(nominalDate(nominalMonth, day), policy), time, title, detail, block, kind, checks, ...(buyerPayment ? { buyerPayment: true } : {}) });
    };
    const previous = shiftMonth(nominalMonth, -1);
    const prep1 = policy.profile === "oleg4" ? 4 : 2;
    const prep2 = policy.profile === "oleg4" ? 19 : 17;
    const infra1 = policy.profile === "oleg4" ? 14 : 12;
    const infra2 = policy.profile === "oleg4" ? 29 : 27;
    monthly("advertiser", 2, "Сверки с рекламодателями за прошлый месяц", 2, "report", policy.profile === "oleg4" ? `Период ${previous}. Ожидаемую и подтверждённую выручку показать отдельно; после подтверждения подготовить сверку байеров.` : `Период ${previous}. Выручка подтверждена рекламодателями; на этих цифрах готовится сверка байеров.`, policy.profile === "oleg4" ? [
      { key: "expected", label: "Ожидаемая выручка выделена отдельно" },
      { key: "confirmed", label: "Подтверждённая выручка сверена с рекламодателями" },
    ] : [{ key: "confirmed", label: "Выручка подтверждена и сверена с рекламодателями" }], policy.preparationTime);
    monthly("buyers-check-1", prep1, "Сверка байеров к выплате 5-го", 2, "report", "Проверить выручку, спенды и прогревы каждого байера; показать уже полученные деньги.", correct, policy.preparationTime);
    monthly("buyers-pay-1", 5, "Выплата байерам за прошлый месяц", 2, "payment", "Задержка выплаты байерам обнуляет блок сверок и выплат за месяц.", payment, policy.paymentTime, true);
    monthly("bonuses-check", infra1, "Расчёт бонусов и KPI сотрудников", 2, "report", `Расчёт за ${previous} сверить с HR и руководителями к выплате 15-го.`, correct, policy.preparationTime);
    monthly("infra-check-1", infra1, "Проверка счетов инфраструктуры к 15-му", 4, "card", "Проверить основание, сумму, отсутствие лишних и задвоенных счетов до оплаты.", correct, policy.preparationTime);
    monthly("bonuses-pay", 15, "Выплата бонусов и KPI сотрудников", 2, "payment", `Выплата за ${previous}, без ошибок.`, payment, policy.paymentTime);
    monthly("infra-pay-1", 15, "Оплата инфраструктуры 15-го", 2, "payment", "Провести проверенные платежи без ошибок.", payment, policy.paymentTime);
    monthly("buyers-check-2", prep2, "Сверка байеров к выплате 20-го", 2, "report", "Проверить выручку, спенды, прогревы и уже полученные деньги.", correct, policy.preparationTime);
    monthly("buyers-pay-2", 20, "Выплата байерам 20-го", 2, "payment", "Задержка выплаты байерам обнуляет блок сверок и выплат за месяц.", payment, policy.paymentTime, true);
    monthly("infra-check-2", infra2, "Проверка счетов инфраструктуры к концу месяца", 4, "card", "Проверить каждый счёт к оплате 30-го; в феврале — к последнему дню месяца.", correct, policy.preparationTime);
    monthly("infra-pay-2", 30, "Оплата инфраструктуры в конце месяца", 2, "payment", "30-го, в феврале — в последний день месяца; нерабочая дата переносится назад.", payment, policy.paymentTime);
    const finalDate = seventhWorkingDate(nominalMonth, policy);
    add({ id: `monthly:${nominalMonth}:final-report`, date: finalDate, time: policy.deadlineTime, title: "Финальный отчёт за прошлый месяц", detail: `P&L, ДДС, ДЗ и КЗ за ${previous} сверены. Срок — седьмой рабочий день.`, block: 2, kind: "report", checks: correct });
    add({ id: `monthly:${nominalMonth}:admin-cost`, date: finalDate, time: policy.deadlineTime, title: "Разбор административных расходов", detail: "Объяснить рост, причины отклонений и возможности сокращения расходов.", block: 4, kind: "report", checks: correct });
    if (policy.payrollDay !== null) monthly("payroll", policy.payrollDay, "Расчёт зарплат с HR", 2, "report", "Расчёт сверить с HR. День установлен в настройках периода.", correct, policy.preparationTime);
    if (policy.profile === "oleg4") {
      const closingMonth = shiftMonth(nominalMonth, -2);
      const closingMonthName = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", timeZone: MOSCOW }).formatToParts(new Date(`${closingMonth}-01T12:00:00Z`)).find(part => part.type === "month")!.value;
      monthly("closing", 5, `Отчёт по закрытию ${closingMonthName}`, 2, "closing", `Отчётный период: ${closingMonth}. Полностью сверить поступления, расходы и итог. Дополнительный штраф 200 $ не включён автоматически — он не был однозначно согласован.`, correct, "16:00");
    }
  }
  return tasks.sort((a, b) => a.deadlineAt.localeCompare(b.deadlineAt) || a.id.localeCompare(b.id));
}

function validTimestamp(value: string | null | undefined): boolean {
  return !!value && Number.isFinite(Date.parse(value));
}

export function taskStatus(task: Task, submission: Submission | undefined, nowIso: string, _policy?: Policy): TaskState {
  if (!validTimestamp(nowIso)) throw new Error("Некорректное время расчёта");
  const now = Date.parse(nowIso);
  const deadline = Date.parse(task.deadlineAt || dateAtTime(task.date, task.time));
  const submitted = submission && validTimestamp(submission.at) && Date.parse(submission.at) <= now ? submission : undefined;
  if (!submitted) return now > deadline ? "overdue" : "pending";
  const complete = task.checks.every(check => typeof submitted.checks[check.key] === "boolean");
  if (!complete) return now > deadline ? "violation" : "pending";
  if (Date.parse(submitted.at) > deadline || task.checks.some(check => submitted.checks[check.key] !== true)) return "violation";
  return "done";
}

function coefficient(violations: number, halfMax: number): number {
  return violations === 0 ? 1 : violations <= halfMax ? 0.5 : 0;
}

function endOfDate(date: string): string {
  return new Date(`${date}T23:59:59.999+03:00`).toISOString();
}

function calendarMonthsBefore(date: string, months: number): string {
  const targetMonth = shiftMonth(date.slice(0, 7), -months);
  return nominalDate(targetMonth, Number(date.slice(-2)));
}

export function analysisDeadline(issue: Issue, policy: Policy): string {
  return endOfDate(addWorkingDays(issue.date, 2, policy));
}

export function calculateMonth(record: MonthRecord, nowIso = new Date().toISOString(), allIssues: Issue[] = []): MonthCalculation {
  const tasks = generateTasks(record.month, record.policy);
  if (!validTimestamp(nowIso)) throw new Error("Некорректное время расчёта");
  if (record.revenuePercent !== null && (!Number.isFinite(record.revenuePercent) || record.revenuePercent < 0 || record.revenuePercent > 100)) throw new Error("Доля выручки должна быть от 0 до 100% либо оставаться незаполненной");
  const policy = record.policy;
  const effectiveNow = record.closure && validTimestamp(record.closure.at) && Date.parse(record.closure.at) <= Date.parse(nowIso) ? record.closure.at : nowIso;
  const now = Date.parse(effectiveNow);
  const today = moscowDate(effectiveNow);
  const taskResults = tasks.map(task => ({ task, submission: record.submissions[task.id], status: taskStatus(task, record.submissions[task.id], effectiveNow, policy) }));
  const violations: Record<BlockId, Set<string>> = { 1: new Set(), 2: new Set(), 3: new Set(), 4: new Set(), 5: new Set() };
  const pending: Record<BlockId, Set<string>> = { 1: new Set(), 2: new Set(), 3: new Set(), 4: new Set(), 5: new Set() };
  const eligible: Record<BlockId, Set<string>> = { 1: new Set(), 2: new Set(), 3: new Set(), 4: new Set(), 5: new Set() };
  let buyerPaymentLate = false;

  for (const { task, submission, status } of taskResults) {
    eligible[task.block].add(task.id);
    if (status === "overdue" || status === "violation") violations[task.block].add(task.id);
    if (status === "pending") pending[task.block].add(task.id);
    const submitted = submission && validTimestamp(submission.at) && Date.parse(submission.at) <= now ? submission : undefined;
    if (task.buyerPayment && ((submitted && Date.parse(submitted.at) > Date.parse(task.deadlineAt)) || (!submitted && now > Date.parse(task.deadlineAt)))) buyerPaymentLate = true;
    if (task.kind === "morning") {
      eligible[4].add(task.id);
      if ((!submitted && now > Date.parse(task.deadlineAt)) || submitted?.checks.cards === false || (submitted && typeof submitted.checks.cards !== "boolean" && now > Date.parse(task.deadlineAt))) violations[4].add(task.id);
      else if (!submitted || typeof submitted.checks.cards !== "boolean") pending[4].add(task.id);
    }
  }

  const uniqueIssues = Array.from(new Map(record.issues.map(issue => [issue.id, issue])).values());
  const issues = uniqueIssues.filter(issue => {
    assertDate(issue.date);
    return !issue.voided && issue.date.slice(0, 7) === record.month && issue.date >= policy.startDate && issue.date <= today;
  });
  const alerts = issues.filter(issue => issue.kind === "alert").length;
  const missingRevenue = policy.profile === "notion5" && (record.revenuePercent === null || !record.revenueEvidence.trim());
  const periodEnded = now > Date.parse(endOfDate(lastDate(record.month)));
  const hasEligiblePeriod = policy.startDate <= lastDate(record.month);
  const warnings: string[] = [];
  if (policy.profile === "notion5") {
    warnings.push("В Notion расходятся 12:00 и 15:00. Применяется время утренней сдачи из настроек периода.");
    warnings.push("Для сроков без времени принято время из настроек; по умолчанию конец дня 23:59. Праздники учитываются только из календаря настроек.");
  } else {
    warnings.push("Поздние правки — отдельная редакция для проверки. Суммы 800/700/300/200 $, сроки ДЗ и КЗ, расширение времени выплат и последствия закрытия требуют подтверждения.");
    warnings.push("Получение денег исключено из KPI. Пропущенные алерты видны в журнале, но дополнительное снижение по ним не назначено без согласованного правила.");
  }
  if (policy.payrollDay === null) warnings.push("Срок расчёта зарплат не установлен: эта задача не включена в оценку.");
  if (policy.startDate > `${record.month}-01` && hasEligiblePeriod) warnings.push(`Учёт начат ${policy.startDate}. Более ранние обязанности не оцениваются; автоматического пропорционального пересчёта бонуса нет.`);
  if (!hasEligiblePeriod || tasks.length === 0) warnings.push("В периоде нет оцениваемых задач. Начисление автоматически не подтверждается.");

  const blocks = ([1, 2, 3, 4, 5] as BlockId[]).filter(id => policy.amounts[id] > 0).map(id => {
    const max = policy.amounts[id];
    if (id === 3) {
      const revenue = record.revenuePercent;
      const c = missingRevenue ? (alerts === 0 ? 1 : alerts === 1 ? 0.5 : 0) : revenue! >= 95 && alerts === 0 ? 1 : revenue! >= 90 && alerts <= 1 ? 0.5 : 0;
      const potential = hasEligiblePeriod && tasks.length > 0 ? max * c : 0;
      return { id, name: BLOCK_NAMES[id], max, violations: alerts, coefficient: c, earned: missingRevenue || !periodEnded ? 0 : potential, potential, pending: missingRevenue || !periodEnded ? 1 : 0, reason: missingRevenue ? "Нужны доля выручки и ссылка или основание по реестру ДЗ" : `Выручка в срок: ${revenue}%; пропущенные алерты: ${alerts}` };
    }
    const count = violations[id].size;
    const c = id === 2 && buyerPaymentLate ? 0 : coefficient(count, id === 2 ? 2 : id === 5 ? 1 : 3);
    const potential = eligible[id].size ? max * c : 0;
    return { id, name: BLOCK_NAMES[id], max, violations: count, coefficient: c, earned: pending[id].size === 0 && periodEnded ? potential : 0, potential, pending: pending[id].size, reason: id === 2 && buyerPaymentLate ? "Задержана выплата байерам: блок обнулён" : `Нарушений: ${count}; ожидается задач: ${pending[id].size}` };
  });

  const history = Array.from(new Map([...allIssues, ...uniqueIssues].map(issue => [issue.id, issue])).values());
  const penaltyRows: PenaltyRow[] = issues.map(issue => {
    const exempt = issue.kind === "alert" || issue.foundByDaniel;
    const due = analysisDeadline(issue, policy);
    const validAnalysis = !!issue.analysis.trim() && validTimestamp(issue.analysisAt) && Date.parse(issue.analysisAt!) <= now;
    const analysisLate = !exempt && (validAnalysis ? Date.parse(issue.analysisAt!) > Date.parse(due) : now > Date.parse(due));
    const lowerDate = calendarMonthsBefore(issue.date, 3);
    const repeatSuggested = issue.kind === "error" && !!issue.category.trim() && history.some(previous => !previous.voided && previous.id !== issue.id && previous.kind === "error" && !previous.foundByDaniel && previous.category.trim() === issue.category.trim() && previous.date >= lowerDate && (previous.date < issue.date || (previous.date === issue.date && previous.createdAt < issue.createdAt)));
    const base = exempt ? 0 : ({ minor: 50, significant: 200, critical: 500 }[issue.severity] ?? 0);
    const multiplier = exempt ? 1 : issue.repeat ? 2 : 1;
    const analysisFee = analysisLate ? 50 : 0;
    return { id: issue.id, base, multiplier, analysisLate, analysisFee, total: base * multiplier + analysisFee, exempt, reason: issue.kind === "alert" ? "Алерт влияет на соответствующий блок, денежного штрафа нет" : issue.foundByDaniel ? "Ошибка найдена Даниилом: денежного штрафа нет" : issue.repeat ? "Подтверждённый повтор: основной штраф удвоен" : "Ошибка обнаружена другим сотрудником", analysisDue: due, repeatSuggested };
  });
  const gross = Math.min(2000, blocks.reduce((sum, block) => sum + block.earned, 0));
  const potentialGross = Math.min(2000, blocks.reduce((sum, block) => sum + block.potential, 0));
  const penaltiesRaw = penaltyRows.reduce((sum, row) => sum + row.total, 0);
  const penaltiesApplied = Math.min(gross, penaltiesRaw);
  const potentialPenaltiesApplied = Math.min(potentialGross, penaltiesRaw);
  const bonus = Math.max(0, gross - penaltiesApplied);
  const potentialBonus = Math.max(0, potentialGross - potentialPenaltiesApplied);
  const provisional = !record.closure || !periodEnded || missingRevenue || policy.draft || tasks.length === 0 || blocks.some(block => block.pending > 0);
  return {
    blocks, penaltiesRaw, penaltiesApplied, potentialPenaltiesApplied, bonus, potentialBonus,
    total: policy.fixed + bonus, potentialTotal: policy.fixed + potentialBonus, gross, potentialGross,
    missingRevenue, overdueCount: taskResults.filter(result => result.status === "overdue").length,
    completedCount: taskResults.filter(result => result.status === "done" || result.status === "violation").length,
    totalTasks: tasks.length, provisional, periodEnded, buyerPaymentLate, penaltyRows, taskResults, warnings, evaluatedAt: effectiveNow,
  };
}

export function emptyMonth(month: string, profile: Profile = "notion5", startDate = moscowDate()): MonthRecord {
  assertMonth(month);
  return { month, policy: defaultPolicy(profile, startDate), submissions: {}, issues: [], revenuePercent: null, revenueEvidence: "", closure: null, revision: 0 };
}
