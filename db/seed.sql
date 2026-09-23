-- Testdata for demoen. ALT ER OPPDIKTET: produkter, artikler og SKU-er finnes ikke hos JDD.
-- Byttes ut med anonymiserte eksempler fra JDD når de kommer.

INSERT INTO products (sku, name, category) VALUES
  ('DEMO-001', 'LED-lysbjelke Nordlys 50 cm',            'Lysbjelke'),
  ('DEMO-002', 'LED-lysbjelke Nordlys 100 cm',           'Lysbjelke'),
  ('DEMO-003', 'Arbeidslys Fjellrev 48W',                'Arbeidslys'),
  ('DEMO-004', 'Arbeidslys Fjellrev 24W rund',           'Arbeidslys'),
  ('DEMO-005', 'Kabelsett for lysbjelke, 1 lampe',       'Kabelsett'),
  ('DEMO-006', 'Kabelsett for lysbjelke, 2 lamper',      'Kabelsett'),
  ('DEMO-007', 'Bilshampo Glans 1 L',                    'Bilpleie'),
  ('DEMO-008', 'Keramisk voks Speil 500 ml',             'Bilpleie'),
  ('DEMO-009', 'LED-baklyssett for tilhenger 13-pol',    'Tilhenger'),
  ('DEMO-010', 'Adapter 7-pol til 13-pol',               'Tilhenger');

-- De tre godkjente startartiklene ligger i en funksjon, slik at
-- POST /api/demo/reset kan legge dem inn på nytt i opprinnelig versjon.
CREATE OR REPLACE FUNCTION seed_demo_articles() RETURNS void AS $$
  INSERT INTO knowledge_articles (product_id, title, problem, solution, tags, approved_by)
  SELECT p.id, a.title, a.problem, a.solution, a.tags, 'Kundesenteret (demo)'
  FROM (VALUES
    ('DEMO-001',
     'Lysbjelke flimrer ved motorstart',
     'Lysbjelken flimrer når motoren startes.',
     'Flimring ved motorstart skyldes som regel spenningsfall mens startmotoren går. '
     || 'Koble lysbjelken via relé på tenningsstrøm, slik at den først får strøm når motoren går. '
     || 'Kontroller også at jordledningen er festet til rent, umalt metall.',
     'lysbjelke, flimring, relé, jording'),
    ('DEMO-003',
     'Arbeidslys dugger innvendig',
     'Arbeidslyset dugger på innsiden av glasset.',
     'Litt dugg etter vask eller regn er normalt og forsvinner når lyset har stått på i 15–20 minutter. '
     || 'Blir duggen stående, sjekk at ventilasjonsmembranen på baksiden ikke er tildekket eller skadet. '
     || 'Står det vann inne i lykten, er tetningen defekt, og lykten skal reklameres.',
     'arbeidslys, dugg, membran, reklamasjon'),
    ('DEMO-009',
     'Tilhengerlys virker ikke etter montering',
     'Lysene på tilhengeren virker ikke etter montering av nytt baklyssett.',
     'Sjekk først jordforbindelsen: jordledningen (pinne 3 på 13-pol) må ha god kontakt med rammen. '
     || 'Kontroller deretter at ledningene er koblet etter koblingsskjemaet, og test bilens tilhengerkontakt med en kontakttester.',
     'tilhenger, baklys, montering, jording')
  ) AS a(sku, title, problem, solution, tags)
  JOIN products p ON p.sku = a.sku;
$$ LANGUAGE sql;

SELECT seed_demo_articles();
