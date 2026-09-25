CREATE TABLE `pdf_attempts` (
	`id` text PRIMARY KEY NOT NULL,
	`project` text NOT NULL,
	`article` text NOT NULL,
	`status` text NOT NULL,
	`reason` text NOT NULL,
	`created` text NOT NULL,
	FOREIGN KEY (`project`) REFERENCES `projects`(`id`) ON UPDATE no action ON DELETE no action
);
