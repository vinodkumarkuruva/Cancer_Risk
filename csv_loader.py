import csv
from sqlalchemy.orm import Session
from sqlalchemy.sql import text
from models import Question, QuestionDependency
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_csv_to_db(db: Session, csv_file: str):
    logger.info(f"Loading CSV from {csv_file}")
    try:
        stored_questions = db.query(Question).order_by(Question.sequence).all()
        stored_ids = [q.id for q in stored_questions]
        expected_ids = [f"Q{i}" for i in range(1, 24)]
        expected_count = 23

        if len(stored_questions) == expected_count and stored_ids == expected_ids:
            logger.info("Questions table already contains correct data, skipping CSV load.")
            return

        if stored_questions:
            logger.info("Incorrect or incomplete questions data, truncating questions and dependencies.")
            db.execute(text("TRUNCATE TABLE questions, question_dependencies RESTART IDENTITY"))
            db.commit()

        with open(csv_file, newline='') as f:
            reader = csv.DictReader(f)
            question_ids = []
            for index, row in enumerate(reader, 1):
                logger.info(f"Processing question {row['Question ID']} (sequence {index})")
                question = Question(
                    id=row["Question ID"],
                    sequence=index,
                    section=row["Section"],
                    sub_section=row["Sub-section"],
                    text=row["Question Text"],
                    type=row["Question Type"],
                    options=row["Options"].split(";") if row["Options"] else None,
                    required=row["Required"] == "Yes",
                    # required = "Yes" if row["Required"] == "Yes" else str(row["Required"]),
                    target_gender=row["Target Gender"] or None,
                    target_age_range=row["Target Age Range"] or None,
                    info_tooltip=row["Info Tooltip"] or None
                )
                db.merge(question)
                question_ids.append(row["Question ID"])

                if row["Conditional Logic"]:
                    logic = row["Conditional Logic"].replace("Show if ", "")
                    if "=" in logic:
                        dep_question_id, dep_answer = logic.split(" = ")
                        dep_answer = dep_answer.strip("'")
                        dependency = QuestionDependency(
                            question_id=row["Question ID"],
                            depends_on_question_id=dep_question_id,
                            depends_on_answer=dep_answer
                        )
                        db.merge(dependency)
                
                db.commit()

            logger.info("CSV data committed to database.")
            stored_questions = db.query(Question).order_by(Question.sequence).all()
            stored_ids = [q.id for q in stored_questions]
            logger.info(f"Stored question IDs: {stored_ids}")
            if stored_ids != question_ids:
                logger.warning(f"Question order mismatch. Expected: {question_ids}, Got: {stored_ids}")
            else:
                logger.info("Question order verified successfully.")
    except Exception as e:
        logger.error(f"Error loading CSV: {e}")
        db.rollback()
        raise