CREATE TABLE `audit_log` (
	`id` text PRIMARY KEY NOT NULL,
	`month` text,
	`action` text NOT NULL,
	`actor_id` text NOT NULL,
	`actor_name` text NOT NULL,
	`at` text NOT NULL,
	`reason` text DEFAULT '' NOT NULL,
	`revision` integer,
	`details` text NOT NULL,
	CONSTRAINT "audit_log_valid_json" CHECK(json_valid("audit_log"."details"))
);
--> statement-breakpoint
CREATE INDEX `audit_log_month_at` ON `audit_log` (`month`,`at`);--> statement-breakpoint
CREATE INDEX `audit_log_at` ON `audit_log` (`at`);--> statement-breakpoint
CREATE TABLE `members` (
	`user_id` text PRIMARY KEY NOT NULL,
	`email` text NOT NULL,
	`display_name` text NOT NULL,
	`role` text NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	`revision` integer DEFAULT 0 NOT NULL,
	`mutation_id` text NOT NULL,
	CONSTRAINT "members_valid_role" CHECK("members"."role" IN ('owner', 'editor', 'contributor', 'viewer')),
	CONSTRAINT "members_valid_revision" CHECK("members"."revision" >= 0)
);
--> statement-breakpoint
CREATE UNIQUE INDEX `members_single_owner` ON `members` (`role`) WHERE "members"."role" = 'owner';--> statement-breakpoint
CREATE TABLE `month_records` (
	`month` text PRIMARY KEY NOT NULL,
	`data` text NOT NULL,
	`revision` integer DEFAULT 0 NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	`mutation_id` text NOT NULL,
	CONSTRAINT "month_records_valid_json" CHECK(json_valid("month_records"."data")),
	CONSTRAINT "month_records_valid_revision" CHECK("month_records"."revision" >= 0)
);
