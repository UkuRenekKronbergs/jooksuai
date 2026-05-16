-- Vorm.ai — Supabase skeem
--
-- Käivita see üks kord:
--   Supabase Dashboard → SQL Editor → New query → kleebi sisu → Run.
--
-- Loob kaks kasutaja-skoobiga tabelit:
--   - athlete_profiles  — sportlase profiil (üks rida per kasutaja)
--   - daily_logs        — päeva-logi (Project Plan §4.3, üks rida per kasutaja+kuupäev)
--
-- Mõlemal on Row-Level Security sees: iga kasutaja näeb / muudab AINULT
-- enda ridu, isegi kui keegi peaks anon-võtmega päringuid manipuleerima.
--
-- Skript on idempotentne — võid uuesti käivitada, ilma andmeid kaotamata.

-- ===== athlete_profiles ==============================================
CREATE TABLE IF NOT EXISTS public.athlete_profiles (
    user_id                   UUID        PRIMARY KEY
                                          REFERENCES auth.users(id)
                                          ON DELETE CASCADE,
    name                      TEXT        NOT NULL,
    age                       INTEGER     NOT NULL CHECK (age BETWEEN 10 AND 100),
    sex                       TEXT        NOT NULL CHECK (sex IN ('M', 'F')),
    max_hr                    INTEGER     NOT NULL CHECK (max_hr BETWEEN 100 AND 240),
    resting_hr                INTEGER     NOT NULL CHECK (resting_hr BETWEEN 20 AND 100),
    training_years            INTEGER     NOT NULL DEFAULT 0 CHECK (training_years >= 0),
    season_goal               TEXT        DEFAULT '',
    personal_bests            JSONB       DEFAULT '{}'::jsonb,
    threshold_pace_min_per_km REAL,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.athlete_profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users select own profile" ON public.athlete_profiles;
DROP POLICY IF EXISTS "Users insert own profile" ON public.athlete_profiles;
DROP POLICY IF EXISTS "Users update own profile" ON public.athlete_profiles;
DROP POLICY IF EXISTS "Users delete own profile" ON public.athlete_profiles;

CREATE POLICY "Users select own profile" ON public.athlete_profiles
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users insert own profile" ON public.athlete_profiles
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users update own profile" ON public.athlete_profiles
    FOR UPDATE USING (auth.uid() = user_id)
                WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users delete own profile" ON public.athlete_profiles
    FOR DELETE USING (auth.uid() = user_id);

-- Hoia updated_at jooksvalt värske.
CREATE OR REPLACE FUNCTION public.tg_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_profiles_updated_at ON public.athlete_profiles;
CREATE TRIGGER trg_profiles_updated_at
    BEFORE UPDATE ON public.athlete_profiles
    FOR EACH ROW EXECUTE FUNCTION public.tg_set_updated_at();


-- ===== daily_logs ====================================================
CREATE TABLE IF NOT EXISTS public.daily_logs (
    user_id              UUID        NOT NULL
                                     REFERENCES auth.users(id)
                                     ON DELETE CASCADE,
    log_date             DATE        NOT NULL,
    recommended_category TEXT        NOT NULL,
    rationale_excerpt    TEXT,
    usefulness           INTEGER     CHECK (usefulness BETWEEN 1 AND 5),
    persuasiveness       INTEGER     CHECK (persuasiveness BETWEEN 1 AND 5),
    followed             TEXT        CHECK (followed IN ('yes', 'no', 'partial')),
    next_session_feeling INTEGER     CHECK (next_session_feeling BETWEEN 1 AND 5),
    notes                TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, log_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_logs_user_date
    ON public.daily_logs (user_id, log_date DESC);

ALTER TABLE public.daily_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users select own logs" ON public.daily_logs;
DROP POLICY IF EXISTS "Users insert own logs" ON public.daily_logs;
DROP POLICY IF EXISTS "Users update own logs" ON public.daily_logs;
DROP POLICY IF EXISTS "Users delete own logs" ON public.daily_logs;

CREATE POLICY "Users select own logs" ON public.daily_logs
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users insert own logs" ON public.daily_logs
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users update own logs" ON public.daily_logs
    FOR UPDATE USING (auth.uid() = user_id)
                WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users delete own logs" ON public.daily_logs
    FOR DELETE USING (auth.uid() = user_id);
