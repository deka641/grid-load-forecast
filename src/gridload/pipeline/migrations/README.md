# Migrations

Files named `NNN_description.sql` are applied in ascending order of `NNN` and recorded in `schema_version` (by number only). The statements of an applied file are never changed; schema changes go into a new file. Comments in applied files are kept current, for example the lists of values a status or scope column takes, since editing them changes no database.
