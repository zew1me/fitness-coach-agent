-- RLS bypass does not replace ordinary SQL privileges. Fresh local bootstraps can
-- create application tables without PostgREST DML grants for service_role, leaving
-- every server-side repository call with SQLSTATE 42501 after the RLS migration.

grant usage on schema public to service_role;
grant select, insert, update, delete on all tables in schema public to service_role;
grant usage, select, update on all sequences in schema public to service_role;

-- Keep tables and sequences created by later postgres-owned migrations usable by
-- the server-side repositories without granting browser roles additional access.
alter default privileges for role postgres in schema public
  grant select, insert, update, delete on tables to service_role;
alter default privileges for role postgres in schema public
  grant usage, select, update on sequences to service_role;
