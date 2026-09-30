CREATE TABLE `screening` (
	`project` text NOT NULL,
	`doi` text NOT NULL,
	`decision` text,
	`answers` text,
	`reasons` text,
	`reason` text,
	`actor` text,
	`version` integer,
	`source` text,
	`ai` text,
	`updated` text NOT NULL,
	PRIMARY KEY(`project`, `doi`),
	FOREIGN KEY (`project`) REFERENCES `projects`(`id`) ON UPDATE no action ON DELETE no action
);
