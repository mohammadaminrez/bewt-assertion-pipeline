from __future__ import annotations

"""Persistent storage for experiment results using SQLite."""

import json
import sqlite3
from pathlib import Path

from ..models import AssertionRecord, AssertionType, ExperimentResult, ErrorCategory, TestRecord
from ..llm.types import LLMCall


class ResultStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app TEXT NOT NULL,
                variant TEXT NOT NULL,
                version TEXT NOT NULL,
                class_name TEXT NOT NULL,
                method_name TEXT NOT NULL,
                treatment TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt TEXT,
                raw_response TEXT,
                generated_assertion TEXT,
                gold_standard TEXT,
                compiles INTEGER DEFAULT 0,
                passes INTEGER DEFAULT 0,
                exact_match INTEGER DEFAULT 0,
                error_category TEXT,
                semantic_similarity REAL DEFAULT 0.0,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(app, version, class_name, treatment, model)
            );

            CREATE INDEX IF NOT EXISTS idx_experiments_app ON experiments(app);
            CREATE INDEX IF NOT EXISTS idx_experiments_treatment ON experiments(treatment);
            CREATE INDEX IF NOT EXISTS idx_experiments_model ON experiments(model);

            CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id INTEGER,
                call_type TEXT NOT NULL,
                app TEXT NOT NULL,
                class_name TEXT NOT NULL,
                method_name TEXT NOT NULL,
                treatment TEXT NOT NULL,
                model TEXT NOT NULL,
                provider TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                user_prompt TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                raw_response TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                cached_input_tokens INTEGER,
                reasoning_tokens INTEGER,
                cost_usd REAL,
                latency_ms INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(experiment_id) REFERENCES experiments(id)
            );

            CREATE INDEX IF NOT EXISTS idx_llm_calls_experiment_id ON llm_calls(experiment_id);
            CREATE INDEX IF NOT EXISTS idx_llm_calls_call_type ON llm_calls(call_type);
            CREATE INDEX IF NOT EXISTS idx_llm_calls_treatment ON llm_calls(treatment);
            CREATE INDEX IF NOT EXISTS idx_llm_calls_model ON llm_calls(model);
            CREATE INDEX IF NOT EXISTS idx_llm_calls_prompt_hash ON llm_calls(prompt_hash);
        """)
        self.conn.commit()

    def save_result(self, result: ExperimentResult) -> int:
        """Save an experiment result. Updates if already exists."""
        data = result.to_dict()
        self.conn.execute("""
            INSERT OR REPLACE INTO experiments
            (app, variant, version, class_name, method_name, treatment, model,
             prompt, raw_response, generated_assertion, gold_standard,
             compiles, passes, exact_match, error_category, semantic_similarity, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["app"], data["variant"], data["version"],
            data["class_name"], data["method_name"],
            data["treatment"], data["model"],
            result.prompt, result.raw_response,
            data["generated_assertion"], data["gold_standard"],
            int(data["compiles"]), int(data["passes"]), int(data["exact_match"]),
            data["error_category"], data["semantic_similarity"], data["notes"],
        ))
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_all_results(self) -> list[dict]:
        """Get all experiment results as dicts."""
        rows = self.conn.execute("SELECT * FROM experiments ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def get_results_by_treatment(self, treatment: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM experiments WHERE treatment = ? ORDER BY id",
            (treatment,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_results_by_model(self, model: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM experiments WHERE model = ? ORDER BY id",
            (model,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_results_by_app(self, app: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM experiments WHERE app = ? ORDER BY id",
            (app,),
        ).fetchall()
        return [dict(row) for row in rows]

    def has_result(self, app: str, version: str, class_name: str, treatment: str, model: str) -> bool:
        """Check if a result already exists (for resumability)."""
        row = self.conn.execute(
            "SELECT 1 FROM experiments WHERE app=? AND version=? AND class_name=? AND treatment=? AND model=?",
            (app, version, class_name, treatment, model),
        ).fetchone()
        return row is not None

    def get_summary(self) -> dict:
        """Get a high-level summary of stored results."""
        total = self.conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
        by_treatment = self.conn.execute(
            "SELECT treatment, COUNT(*), SUM(compiles), SUM(passes), SUM(exact_match) "
            "FROM experiments GROUP BY treatment"
        ).fetchall()
        by_model = self.conn.execute(
            "SELECT model, COUNT(*), SUM(compiles), SUM(passes), SUM(exact_match) "
            "FROM experiments GROUP BY model"
        ).fetchall()

        return {
            "total": total,
            "by_treatment": {
                row[0]: {"count": row[1], "compiles": row[2], "passes": row[3], "exact": row[4]}
                for row in by_treatment
            },
            "by_model": {
                row[0]: {"count": row[1], "compiles": row[2], "passes": row[3], "exact": row[4]}
                for row in by_model
            },
        }

    def save_llm_call(self, call: LLMCall) -> int:
        """Save one auditable LLM call trace."""
        data = call.to_dict()
        cursor = self.conn.execute("""
            INSERT INTO llm_calls
            (experiment_id, call_type, app, class_name, method_name, treatment, model, provider,
             system_prompt, user_prompt, prompt_hash, raw_response,
             input_tokens, output_tokens, total_tokens, cached_input_tokens,
             reasoning_tokens, cost_usd, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["experiment_id"], data["call_type"], data["app"], data["class_name"],
            data["method_name"], data["treatment"], data["model"], data["provider"],
            data["system_prompt"], data["user_prompt"], data["prompt_hash"], data["raw_response"],
            data["input_tokens"], data["output_tokens"], data["total_tokens"],
            data["cached_input_tokens"], data["reasoning_tokens"], data["cost_usd"],
            data["latency_ms"],
        ))
        self.conn.commit()
        return int(cursor.lastrowid)

    def get_llm_calls(
        self,
        *,
        call_type: str | None = None,
        app: str | None = None,
        class_name: str | None = None,
        treatment: str | None = None,
        model: str | None = None,
    ) -> list[dict]:
        """Get LLM call traces, optionally filtered by research metadata."""
        filters = {
            "call_type": call_type,
            "app": app,
            "class_name": class_name,
            "treatment": treatment,
            "model": model,
        }
        clauses = [f"{key} = ?" for key, value in filters.items() if value is not None]
        values = [value for value in filters.values() if value is not None]
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM llm_calls{where} ORDER BY id",
            values,
        ).fetchall()
        return [dict(row) for row in rows]

    def load_experiment_results(self) -> list[ExperimentResult]:
        """Reconstruct ExperimentResult objects from stored DB rows."""
        rows = self.get_all_results()
        results = []
        for row in rows:
            # Build a stub TestRecord with gold_standard parsed back into an AssertionRecord
            gold_text = row.get("gold_standard", "") or ""
            assertions = []
            if gold_text.strip():
                for line in gold_text.split("\n"):
                    line = line.strip()
                    if line:
                        assertions.append(AssertionRecord(
                            assertion_type=AssertionType.ASSERT_EQUALS,
                            full_text=line,
                            start_line=0, end_line=0,
                        ))

            record = TestRecord(
                app=row["app"], variant=row["variant"], version=row["version"],
                file_path="", class_name=row["class_name"],
                method_name=row["method_name"],
                assertions=assertions,
            )
            results.append(ExperimentResult(
                test_record=record,
                treatment=row["treatment"], model=row["model"],
                prompt=row.get("prompt", "") or "",
                raw_response=row.get("raw_response", "") or "",
                generated_assertion=row.get("generated_assertion", "") or "",
                compiles=bool(row.get("compiles", 0)),
                passes=bool(row.get("passes", 0)),
                exact_match=bool(row.get("exact_match", 0)),
                error_category=ErrorCategory(row.get("error_category", "not_executable")),
                semantic_similarity=row.get("semantic_similarity", 0.0) or 0.0,
                notes=row.get("notes", "") or "",
            ))
        return results

    def update_classification(self, app: str, class_name: str, treatment: str, model: str,
                              error_category: str, notes: str = "") -> bool:
        """Update the error_category (and optionally notes) from manual annotation."""
        cursor = self.conn.execute(
            "UPDATE experiments SET error_category=?, notes=? "
            "WHERE app=? AND class_name=? AND treatment=? AND model=?",
            (error_category, notes, app, class_name, treatment, model),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self.conn.close()
