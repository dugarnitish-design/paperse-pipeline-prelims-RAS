-- pyq_polls — log of daily Telegram PYQ quiz polls (pipelines/pyq_poll_bot.py, STEP 6)
-- Applied to the paperse project (nunbpwaxqqgfxrosqfhw). Re-runnable.

create table if not exists public.pyq_polls (
  id                bigint generated always as identity primary key,
  date              date not null,                 -- daily_ca_items date the stories came from
  pyq_id            uuid references public.questions(id),
  news_story_title  text,                           -- set for type='relevant', null for 'revision'
  type              text check (type in ('relevant','revision')),
  poll_message_id   bigint,                         -- Telegram message_id of the posted poll
  posted_at         timestamptz not null default now()
);

create index if not exists pyq_polls_date_idx      on public.pyq_polls(date);
create index if not exists pyq_polls_posted_at_idx on public.pyq_polls(posted_at);
create index if not exists pyq_polls_pyq_id_idx    on public.pyq_polls(pyq_id);
