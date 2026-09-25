CREATE TABLE `project_members` (
	`project` text NOT NULL,
	`email` text NOT NULL,
	`user_id` text,
	`name` text,
	`role` text NOT NULL,
	`status` text NOT NULL,
	`invited_by` text NOT NULL,
	`created` text NOT NULL,
	`responded` text,
	PRIMARY KEY(`project`, `email`),
	FOREIGN KEY (`project`) REFERENCES `projects`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE INDEX `idx_project_members_user_status` ON `project_members` (`user_id`,`status`);--> statement-breakpoint
CREATE INDEX `idx_project_members_email_status` ON `project_members` (`email`,`status`);