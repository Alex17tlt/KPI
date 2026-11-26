import { sql } from "drizzle-orm";
import { check, index, integer, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

/** Shared ledger. Access is always checked against the authenticated ChatGPT user. */
export const members = sqliteTable("members", {
  userId: text("user_id").primaryKey(),
  email: text("email").notNull(),
  displayName: text("display_name").notNull(),
  role: text("role", { enum: ["owner", "editor", "contributor", "viewer"] }).notNull(),
  createdAt: text("created_at").notNull(),
  updatedAt: text("updated_at").notNull(),
  revision: integer("revision").notNull().default(0),
  mutationId: text("mutation_id").notNull(),
}, (table) => [
  check("members_valid_role", sql`${table.role} IN ('owner', 'editor', 'contributor', 'viewer')`),
  check("members_valid_revision", sql`${table.revision} >= 0`),
  uniqueIndex("members_single_owner").on(table.role).where(sql`${table.role} = 'owner'`),
]);

/** Each month retains its own rule snapshot and a compare-and-swap revision. */
export const monthRecords = sqliteTable("month_records", {
  month: text("month").primaryKey(),
  data: text("data").notNull(),
  revision: integer("revision").notNull().default(0),
  createdAt: text("created_at").notNull(),
  updatedAt: text("updated_at").notNull(),
  mutationId: text("mutation_id").notNull(),
}, (table) => [
  check("month_records_valid_json", sql`json_valid(${table.data})`),
  check("month_records_valid_revision", sql`${table.revision} >= 0`),
]);

/** Append-only in the API: edits are new events; there is no update/delete endpoint. */
export const auditLog = sqliteTable("audit_log", {
  id: text("id").primaryKey(),
  month: text("month"),
  action: text("action").notNull(),
  actorId: text("actor_id").notNull(),
  actorName: text("actor_name").notNull(),
  at: text("at").notNull(),
  reason: text("reason").notNull().default(""),
  revision: integer("revision"),
  details: text("details").notNull(),
}, (table) => [
  check("audit_log_valid_json", sql`json_valid(${table.details})`),
  index("audit_log_month_at").on(table.month, table.at),
  index("audit_log_at").on(table.at),
]);
