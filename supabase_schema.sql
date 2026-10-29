-- Private, single-row persistence for Nha Trang Life News Bot.
-- Run this once in the Supabase SQL Editor of your own project.
create table if not exists public.bot_state (
    id text primary key check (id = 'main'),
    state jsonb not null,
    updated_at timestamptz not null default now()
);

alter table public.bot_state enable row level security;

-- The bot authenticates with a server-side secret stored only in GitHub Actions.
-- Do not grant direct table access to public/anon/authenticated clients.
revoke all on table public.bot_state from anon, authenticated;
grant select, insert, update on table public.bot_state to service_role;
