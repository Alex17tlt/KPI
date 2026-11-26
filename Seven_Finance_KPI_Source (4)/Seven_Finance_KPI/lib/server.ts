import { env } from "cloudflare:workers";
import { getChatGPTUser, type ChatGPTUser } from "@/app/chatgpt-auth";
import { defaultPolicy, generateTasks, moscowDate, validatePolicy, type BlockId, type MonthRecord, type Policy, type Profile } from "@/lib/kpi";

export type Role = "owner" | "editor" | "contributor" | "viewer";
export type Member = {
  userId: string; email: string; displayName: string; role: Role;
  createdAt: string; updatedAt: string; revision: number;
};
export type Context = { db: D1Database; user: ChatGPTUser; member: Member };
export type JsonObject = Record<string, unknown>;

const MEMBER_SELECT = "user_id AS userId, email, display_name AS displayName, role, created_at AS createdAt, updated_at AS updatedAt, revision";
export const EDITORS: Role[] = ["owner", "editor"];
export const CONTRIBUTORS: Role[] = ["owner", "editor", "contributor"];
const TIME_KEYS = ["morningTime", "eveningTime", "dzTime", "kzTime", "paymentTime", "deadlineTime", "preparationTime"] as const;
const POLICY_KEYS = ["profile", "fixed", "amounts", ...TIME_KEYS, "payrollDay", "holidays", "workingDates", "startDate", "draft"];

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export function requireRole(context: Context, allowed: Role[]): void {
  if (!allowed.includes(context.member.role)) throw new ApiError(403, "У вашей роли нет прав на это действие.");
}

export function object(value: unknown, label = "Данные"): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ApiError(400, `${label}: ожидается объект.`);
  return value as JsonObject;
}

export function onlyKeys(value: JsonObject, allowed: readonly string[], label = "Запрос"): void {
  const unknown = Object.keys(value).filter(key => !allowed.includes(key));
  if (unknown.length) throw new ApiError(400, `${label}: неизвестные поля ${unknown.join(", ")}.`);
}

export function text(value: unknown, label: string, max = 4000, required = true): string {
  if (typeof value !== "string") throw new ApiError(400, `${label}: ожидается текст.`);
  const result = value.trim();
  if ((required && !result) || result.length > max) throw new ApiError(400, `${label}: ${required ? "заполните поле, " : ""}не более ${max} символов.`);
  return result;
}

export function bool(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") throw new ApiError(400, `${label}: требуется значение да или нет.`);
  return value;
}

export function monthValue(value: unknown): string {
  if (typeof value !== "string" || !/^(?:20\d{2}|2100)-(?:0[1-9]|1[0-2])$/.test(value) || Number(value.slice(0, 4)) < 2020) {
    throw new ApiError(400, "Укажите месяц в формате ГГГГ-ММ, с 2020 по 2100 год.");
  }
  return value;
}

export function dateValue(value: unknown, label = "Дата"): string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new ApiError(400, `${label}: нужен формат ГГГГ-ММ-ДД.`);
  const time = new Date(`${value}T12:00:00Z`).getTime();
  if (!Number.isFinite(time) || new Date(time).toISOString().slice(0, 10) !== value || Number(value.slice(0, 4)) < 2020 || Number(value.slice(0, 4)) > 2100) {
    throw new ApiError(400, `${label}: некорректная календарная дата.`);
  }
  return value;
}

export function lastDay(month: string): string {
  return new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0, 12)).toISOString().slice(0, 10);
}

export function revisionValue(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new ApiError(400, "Не указана корректная версия данных. Обновите страницу.");
  return value as number;
}

export function profileValue(value: unknown): Profile {
  if (value !== "notion5" && value !== "oleg4") throw new ApiError(400, "Неизвестная редакция правил.");
  return value;
}

function money(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1e9 || Math.abs(Math.round(value * 100) - value * 100) > 0.00001) {
    throw new ApiError(400, `${label}: нужно неотрицательное число с точностью до центов.`);
  }
  return value;
}

function dates(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.length > 400) throw new ApiError(400, `${label}: список должен содержать не более 400 дат.`);
  return [...new Set(value.map(item => dateValue(item, label)))].sort();
}

export function policyValue(value: unknown, month: string): Policy {
  const raw = object(value, "Правила");
  onlyKeys(raw, POLICY_KEYS, "Правила");
  const profile = profileValue(raw.profile);
  const startDate = dateValue(raw.startDate, "Начало учёта");
  if (startDate > lastDay(month)) throw new ApiError(400, "Начало учёта не может быть позже конца выбранного месяца.");
  const amountsRaw = object(raw.amounts, "Суммы блоков");
  onlyKeys(amountsRaw, ["1", "2", "3", "4", "5"], "Суммы блоков");
  const amounts = {} as Record<BlockId, number>;
  for (const id of [1, 2, 3, 4, 5] as BlockId[]) {
    amounts[id] = money(amountsRaw[String(id)], `Блок ${id}`);
    if (amounts[id] === 0 && !(profile === "oleg4" && id === 3)) throw new ApiError(400, `Сумма активного блока ${id} должна быть больше нуля.`);
  }
  const policy = { ...defaultPolicy(profile, startDate), fixed: money(raw.fixed, "Фикс"), amounts, draft: bool(raw.draft, "Статус проекта правил") };
  for (const key of TIME_KEYS) {
    if (typeof raw[key] !== "string" || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(raw[key] as string)) throw new ApiError(400, `Время ${key}: нужен формат ЧЧ:ММ.`);
    policy[key] = raw[key] as string;
  }
  if (raw.payrollDay !== null && (!Number.isInteger(raw.payrollDay) || (raw.payrollDay as number) < 1 || (raw.payrollDay as number) > 31)) {
    throw new ApiError(400, "День расчёта зарплаты: число от 1 до 31 либо пустое значение.");
  }
  policy.payrollDay = raw.payrollDay as number | null;
  policy.holidays = dates(raw.holidays, "Нерабочие даты");
  policy.workingDates = dates(raw.workingDates, "Дополнительные рабочие даты");
  if (policy.holidays.some(date => policy.workingDates.includes(date))) throw new ApiError(400, "Одна дата не может одновременно быть рабочей и нерабочей.");
  try { validatePolicy(policy); generateTasks(month, policy); } catch (error) {
    throw new ApiError(400, error instanceof Error ? error.message : "Некорректные правила.");
  }
  return policy;
}

export function ensureOrigin(request: Request): void {
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) throw new ApiError(403, "Действие разрешено только со страницы этого инструмента.");
  if (request.headers.get("sec-fetch-site") === "cross-site") throw new ApiError(403, "Запрос с другого сайта отклонён.");
}

export async function requestBody(request: Request): Promise<JsonObject> {
  if (!(request.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) throw new ApiError(415, "Нужен запрос в формате JSON.");
  if (Number(request.headers.get("content-length") ?? "0") > 100_000) throw new ApiError(413, "Запрос слишком большой.");
  const raw = await request.text();
  if (raw.length > 100_000) throw new ApiError(413, "Запрос слишком большой.");
  try { return object(JSON.parse(raw), "Запрос"); } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(400, "Не удалось прочитать JSON запроса.");
  }
}

/** The private Site has a single first owner; subsequent signed-in visitors are viewers. */
export async function authenticate(): Promise<Context> {
  const user = await getChatGPTUser();
  if (!user) throw new ApiError(401, "Войдите через ChatGPT, чтобы открыть общий учёт.");
  if (!env.DB) throw new ApiError(503, "Хранилище пока недоступно. Попробуйте обновить страницу позже.");
  const db = env.DB;
  const existing = await db.prepare(`SELECT ${MEMBER_SELECT} FROM members WHERE user_id = ?`).bind(user.userId).first<Member>();
  if (existing) return { db, user, member: existing };
  const at = new Date().toISOString();
  const mutationId = crypto.randomUUID();
  const params = [user.userId, user.email, user.displayName, at, at, mutationId];
  await db.batch([
    db.prepare("INSERT OR IGNORE INTO members (user_id, email, display_name, role, created_at, updated_at, revision, mutation_id) SELECT ?, ?, ?, 'owner', ?, ?, 0, ? WHERE NOT EXISTS (SELECT 1 FROM members)").bind(...params),
    db.prepare("INSERT OR IGNORE INTO members (user_id, email, display_name, role, created_at, updated_at, revision, mutation_id) VALUES (?, ?, ?, 'viewer', ?, ?, 0, ?)").bind(...params),
    db.prepare("INSERT INTO audit_log (id, month, action, actor_id, actor_name, at, reason, revision, details) SELECT ?, NULL, 'member_join', ?, ?, ?, '', 0, json_object('role', role) FROM members WHERE user_id = ? AND mutation_id = ?")
      .bind(mutationId, user.userId, user.displayName, at, user.userId, mutationId),
  ]);
  const member = await db.prepare(`SELECT ${MEMBER_SELECT} FROM members WHERE user_id = ?`).bind(user.userId).first<Member>();
  if (!member) throw new ApiError(503, "Не удалось определить права доступа.");
  return { db, user, member };
}

export async function getMonth(context: Context, month: string): Promise<MonthRecord> {
  const row = await context.db.prepare("SELECT data, revision FROM month_records WHERE month = ?").bind(month).first<{ data: string; revision: number }>();
  if (!row) throw new ApiError(404, "Месяц ещё не создан.");
  return { ...JSON.parse(row.data) as MonthRecord, revision: row.revision };
}

export async function snapshot(context: Context) {
  const queries = [
    context.db.prepare("SELECT data, revision FROM month_records ORDER BY month DESC"),
    context.db.prepare("SELECT id, month, action, actor_id AS actorId, actor_name AS actorName, at, reason, revision, details FROM audit_log ORDER BY at DESC, rowid DESC LIMIT 100"),
  ];
  if (context.member.role === "owner") queries.push(context.db.prepare(`SELECT ${MEMBER_SELECT} FROM members ORDER BY created_at, user_id`));
  const results = await context.db.batch(queries);
  const months = (results[0].results as { data: string; revision: number }[]).map(row => ({ ...JSON.parse(row.data) as MonthRecord, revision: row.revision }));
  const audit = (results[1].results as (JsonObject & { details: string })[]).map(row => ({ ...row, details: JSON.parse(row.details) as unknown }));
  return {
    user: { userId: context.user.userId, email: context.user.email, displayName: context.user.displayName },
    role: context.member.role,
    now: new Date().toISOString(),
    months,
    members: context.member.role === "owner" ? results[2].results as Member[] : [],
    audit,
  };
}

export async function saveMonth(context: Context, record: MonthRecord, expectedRevision: number, action: string, reason: string, details: unknown, allowed: Role[] = EDITORS): Promise<void> {
  const at = new Date().toISOString();
  const mutationId = crypto.randomUUID();
  record.revision = expectedRevision + 1;
  const data = JSON.stringify(record);
  if (data.length > 1_500_000) throw new ApiError(413, "В этом месяце слишком много данных. Сократите длину новых комментариев.");
  const roles = allowed.map(() => "?").join(",");
  const results = await context.db.batch([
    context.db.prepare(`UPDATE month_records SET data = ?, revision = ?, updated_at = ?, mutation_id = ? WHERE month = ? AND revision = ? AND EXISTS (SELECT 1 FROM members WHERE user_id = ? AND role IN (${roles}))`)
      .bind(data, record.revision, at, mutationId, record.month, expectedRevision, context.user.userId, ...allowed),
    context.db.prepare("INSERT INTO audit_log (id, month, action, actor_id, actor_name, at, reason, revision, details) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM month_records WHERE month = ? AND mutation_id = ?)")
      .bind(mutationId, record.month, action, context.user.userId, context.user.displayName, at, reason, record.revision, JSON.stringify(details), record.month, mutationId),
  ]);
  if (results[0].meta.changes !== 1) throw new ApiError(409, "Данные или права доступа изменились. Обновите страницу и повторите действие.");
}

export async function insertMonth(context: Context, record: MonthRecord): Promise<void> {
  const at = new Date().toISOString();
  const mutationId = crypto.randomUUID();
  const results = await context.db.batch([
    context.db.prepare("INSERT OR IGNORE INTO month_records (month, data, revision, created_at, updated_at, mutation_id) SELECT ?, ?, 0, ?, ?, ? WHERE EXISTS (SELECT 1 FROM members WHERE user_id = ? AND role IN ('owner', 'editor'))")
      .bind(record.month, JSON.stringify(record), at, at, mutationId, context.user.userId),
    context.db.prepare("INSERT INTO audit_log (id, month, action, actor_id, actor_name, at, reason, revision, details) SELECT ?, ?, 'createMonth', ?, ?, ?, '', 0, ? WHERE EXISTS (SELECT 1 FROM month_records WHERE month = ? AND mutation_id = ?)")
      .bind(mutationId, record.month, context.user.userId, context.user.displayName, at, JSON.stringify({ policy: record.policy }), record.month, mutationId),
  ]);
  if (results[0].meta.changes !== 1) throw new ApiError(409, "Такой месяц уже существует либо изменились права доступа. Обновите страницу.");
}

export async function changeRole(context: Context, payload: JsonObject): Promise<void> {
  requireRole(context, ["owner"]);
  onlyKeys(payload, ["action", "userId", "role", "memberRevision", "reason", "month", "revision"]);
  const userId = text(payload.userId, "Участник", 300);
  if (userId === context.user.userId) throw new ApiError(400, "Владелец не может изменить собственную роль.");
  if (!["viewer", "contributor", "editor"].includes(payload.role as string)) throw new ApiError(400, "Доступны роли: viewer, contributor, editor.");
  const target = await context.db.prepare(`SELECT ${MEMBER_SELECT} FROM members WHERE user_id = ?`).bind(userId).first<Member>();
  if (!target) throw new ApiError(404, "Участник ещё не входил в инструмент.");
  if (target.role === "owner") throw new ApiError(403, "Роль владельца менять нельзя.");
  const revision = payload.memberRevision === undefined ? target.revision : revisionValue(payload.memberRevision);
  if (revision !== target.revision) throw new ApiError(409, "Роль участника уже изменена. Обновите страницу.");
  if (target.role === payload.role) throw new ApiError(409, "Эта роль уже назначена.");
  const at = new Date().toISOString();
  const mutationId = crypto.randomUUID();
  const reason = payload.reason === undefined ? "Изменение прав владельцем" : text(payload.reason, "Причина", 2000);
  const results = await context.db.batch([
    context.db.prepare("UPDATE members SET role = ?, revision = revision + 1, updated_at = ?, mutation_id = ? WHERE user_id = ? AND role <> 'owner' AND revision = ? AND EXISTS (SELECT 1 FROM members WHERE user_id = ? AND role = 'owner')")
      .bind(payload.role as string, at, mutationId, userId, revision, context.user.userId),
    context.db.prepare("INSERT INTO audit_log (id, month, action, actor_id, actor_name, at, reason, revision, details) SELECT ?, NULL, 'role', ?, ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM members WHERE user_id = ? AND mutation_id = ?)")
      .bind(mutationId, context.user.userId, context.user.displayName, at, reason, revision + 1, JSON.stringify({ userId, before: target.role, after: payload.role }), userId, mutationId),
  ]);
  if (results[0].meta.changes !== 1) throw new ApiError(409, "Роль участника уже изменена. Обновите страницу.");
}

export function today(): string { return moscowDate(); }

export function response(value: unknown, status = 200): Response {
  return Response.json(value, { status, headers: { "Cache-Control": "no-store, private", "Vary": "Cookie", "X-Content-Type-Options": "nosniff" } });
}

export function errorResponse(error: unknown): Response {
  if (error instanceof ApiError) return response({ error: error.message }, error.status);
  console.error("KPI API failure", error);
  return response({ error: "Не удалось сохранить или прочитать данные. Обновите страницу; если ошибка повторится, обратитесь к владельцу." }, 500);
}
