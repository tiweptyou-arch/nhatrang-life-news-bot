-- Safe, idempotent hardening for the existing private JSONB state table.
-- The application keeps its existing single-row bot_state structure; no user data is dropped.
alter table if exists public.bot_state enable row level security;

revoke all on table public.bot_state from anon, authenticated;
grant select, insert, update on table public.bot_state to service_role;

comment on table public.bot_state is
  'Private editorial bot state. Contains review queue, source URLs, usage ledger, and weather state.';
