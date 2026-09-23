-- Databaseskjema for JDD kunnskapsdatabase (prototype).
-- Kjøres automatisk av Postgres-containeren første gang databasen opprettes.
-- Rå e-post lagres aldri; bare anonymisert tekst går videre.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE products (
  id          INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  sku         VARCHAR(50) UNIQUE NOT NULL,
  name        VARCHAR(200) NOT NULL,
  category    VARCHAR(100) NOT NULL   -- f.eks. Lysbjelke, Arbeidslys, Bilpleie, Tilhenger
);

-- Anonymisert kopi av hver e-posttråd som er behandlet
CREATE TABLE email_items (
  id               INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_msg_id    VARCHAR(200) UNIQUE NOT NULL,  -- Mailpit-ID, hindrer dobbel behandling
  anonymized_text  TEXT NOT NULL,
  status           VARCHAR(20) NOT NULL,          -- received, anonymized, extracted, proposed, failed
  created_at       TIMESTAMP NOT NULL DEFAULT now()
);

-- Godkjente artikler i kunnskapsdatabasen
CREATE TABLE knowledge_articles (
  id          INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  product_id  INT REFERENCES products(id),
  title       VARCHAR(200) NOT NULL,
  problem     TEXT NOT NULL,
  solution    TEXT NOT NULL,
  tags        VARCHAR(300),
  version     INT NOT NULL DEFAULT 1,
  approved_by VARCHAR(100) NOT NULL,
  approved_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at  TIMESTAMP NOT NULL DEFAULT now()
);

-- KI-forslag som venter på godkjenning
CREATE TABLE proposals (
  id                  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email_item_id       INT REFERENCES email_items(id),
  product_id          INT REFERENCES products(id),
  title               VARCHAR(200) NOT NULL,
  problem             TEXT NOT NULL,
  solution            TEXT NOT NULL,
  tags                VARCHAR(300),
  proposal_type       VARCHAR(20) NOT NULL,  -- new, update
  matched_article_id  INT REFERENCES knowledge_articles(id),
  similarity          NUMERIC(4,3),
  status              VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, approved, rejected
  reviewed_by         VARCHAR(100),
  reviewed_at         TIMESTAMP,
  created_at          TIMESTAMP NOT NULL DEFAULT now()
);

-- Hendelseslogg som viser dataflyten steg for steg
CREATE TABLE pipeline_events (
  id             INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email_item_id  INT REFERENCES email_items(id),
  step           VARCHAR(30) NOT NULL,  -- received, anonymized, extracted, duplicate_check, proposed, approved, rejected
  detail         TEXT,
  created_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_articles_problem_trgm ON knowledge_articles USING gin (problem gin_trgm_ops);
