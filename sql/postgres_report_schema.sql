CREATE TABLE IF NOT EXISTS report_entry (
    id char(32) PRIMARY KEY,
    content text NOT NULL,
    type text NOT NULL,
    CONSTRAINT report_entry_id_format CHECK (id ~ '^[0-9a-f]{32}$')
);

CREATE TABLE IF NOT EXISTS report_chart (
    report_id char(32) NOT NULL REFERENCES report_entry(id) ON DELETE CASCADE,
    insert_key integer NOT NULL CHECK (insert_key > 0),
    type text NOT NULL,
    data_json jsonb NOT NULL,
    PRIMARY KEY (report_id, insert_key)
);

CREATE INDEX IF NOT EXISTS report_chart_report_id_idx ON report_chart (report_id);
