from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy.sql import text
from pydantic import BaseModel
from typing import Dict, List, Optional
from models import Question, QuestionDependency, PatientAnswer
from database import get_db, Base, engine, SessionLocal
from csv_loader import load_csv_to_db
import os
import logging
import json
from sqlalchemy import desc, func

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

@app.on_event("startup")
def on_startup():
    logger.info("Starting up and creating database tables...")
    try:
        Base.metadata.create_all(bind=engine)
        logger.info(f"Tables registered with metadata: {Base.metadata.tables.keys()}")
        with engine.connect() as conn:
            result = conn.execute(text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'questions')"))
            table_exists = result.scalar()
            if table_exists:
                logger.info("Table 'questions' exists.")
            else:
                logger.error("Table 'questions' was not created.")
                raise Exception("Failed to create 'questions' table.")
        
        logger.info("Tables created successfully.")
        csv_path = "Refined_Cancer_Risk_Questionnaire.csv"
        if os.path.exists(csv_path):
            with SessionLocal() as db:
                load_csv_to_db(db, csv_path)
                logger.info("CSV data loaded successfully.")
        else:
            logger.error(f"CSV file not found at {csv_path}")
            raise FileNotFoundError(f"CSV file not found: {csv_path}")
    except Exception as e:
        logger.error(f"Error during startup: {e}")
        raise

# Pydantic models
class PatientInput(BaseModel):
    patient_id: str
    gender: str
    age: int
    previous_answers: Dict[str, str]

class QuestionResponse(BaseModel):
    id: str
    text: str
    type: str
    options: Optional[List[str]]
    required: bool

class NextQuestionResponse(BaseModel):
    next_question: Optional[QuestionResponse]

class PatientDetail(BaseModel):
    question_id: str
    text: str
    type: str
    answer: str

class PatientDetailsResponse(BaseModel):
    patient_id: str
    details: List[PatientDetail]

def is_age_in_range(age: int, age_range: str) -> bool:
    if age_range == "Any":
        return True
    if "-" in age_range:
        min_age, max_age = map(int, age_range.split("-"))
        return min_age <= age <= max_age
    if "+" in age_range:
        min_age = int(age_range.replace("+", ""))
        return age >= min_age
    return False

@app.post("/next-question", response_model=NextQuestionResponse)
def get_next_question(input: PatientInput, db: Session = Depends(get_db)):
    try:
        valid_genders = ["Male", "Female", "Intersex"]
        if input.gender not in valid_genders:
            raise HTTPException(status_code=400, detail=f"Invalid gender. Must be one of {valid_genders}")
        if input.age < 0:
            raise HTTPException(status_code=400, detail="Age must be non-negative")

        logger.info(f"Processing previous_answers for patient {input.patient_id}: {input.previous_answers}")

        db_answers = db.query(PatientAnswer).filter(PatientAnswer.patient_id == input.patient_id).all()
        existing_answers = {ans.question_id: ans.answer for ans in db_answers}

        all_answers = existing_answers.copy()
        all_answers.update(input.previous_answers)

        all_answers["Q1"] = input.gender
        all_answers["Q2"] = str(input.age)

        # Validate required questions and Q4 when Q3 = 'Yes'
        questions = db.query(Question).order_by(Question.sequence).all()
        for question in questions:
            if question.required and question.id in all_answers:
                answer = all_answers[question.id]
                if not answer or (isinstance(answer, str) and answer.strip() == "") or (isinstance(answer, list) and not answer):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Required question {question.id} ('{question.text}') must have a valid answer."
                    )
            elif question.required and question.id not in all_answers:
                continue
            if question.id == "Q4" and "Q3" in all_answers and all_answers["Q3"] == "Yes":
                if question.id in all_answers:
                    answer = all_answers[question.id]
                    if not answer or (isinstance(answer, str) and answer.strip() == "") or (isinstance(answer, list) and not answer):
                        raise HTTPException(
                            status_code=400,
                            detail="Required question Q4 ('If yes, what type(s) of cancer did they have?') must have at least one option selected since Q3 is 'Yes'."
                        )

        # Save or update answers
        for qid, answer in input.previous_answers.items():
            question = db.query(Question).filter(Question.id == qid).first()
            if not answer or answer.strip() == "":
                if (question and question.required) or (qid == "Q4" and "Q3" in all_answers and all_answers["Q3"] == "Yes"):
                    logger.warning(f"Empty answer provided for required question {qid}")
                    raise HTTPException(
                        status_code=400,
                        detail=f"Answer for required question {qid} ('{question.text}') cannot be empty"
                    )
                # Apply defaults for Q5 and Q6
                if qid == "Q5":
                    answer = "No"
                    logger.info(f"Applying default 'No' for Q5")
                elif qid == "Q6" and "Q5" in all_answers and all_answers["Q5"] == "Yes":
                    answer = json.dumps(["Unknown"])
                    logger.info(f"Applying default 'Unknown' for Q6")
                else:
                    answer = "[]" if question and question.type == "Checkbox" else ""
            existing_answer = db.query(PatientAnswer).filter(
                PatientAnswer.patient_id == input.patient_id,
                PatientAnswer.question_id == qid
            ).first()
            if existing_answer:
                existing_answer.answer = json.dumps(answer.split(",")) if "," in str(answer) and question.type == "Checkbox" else answer
            else:
                db.add(PatientAnswer(
                    patient_id=input.patient_id,
                    question_id=qid,
                    answer=json.dumps(answer.split(",")) if "," in str(answer) and question.type == "Checkbox" else answer
                ))
        # Ensure defaults for Q5/Q6 if they were shown but not answered
        if "Q5" in all_answers and (not all_answers["Q5"] or all_answers["Q5"].strip() == ""):
            existing_answer = db.query(PatientAnswer).filter(
                PatientAnswer.patient_id == input.patient_id,
                PatientAnswer.question_id == "Q5"
            ).first()
            if existing_answer:
                existing_answer.answer = "No"
            else:
                db.add(PatientAnswer(
                    patient_id=input.patient_id,
                    question_id="Q5",
                    answer="No"
                ))
            logger.info(f"Applied default 'No' for Q5 in all_answers")
        if "Q6" in all_answers and "Q5" in all_answers and all_answers["Q5"] == "Yes" and (not all_answers["Q6"] or all_answers["Q6"].strip() == "" or all_answers["Q6"] == "[]"):
            existing_answer = db.query(PatientAnswer).filter(
                PatientAnswer.patient_id == input.patient_id,
                PatientAnswer.question_id == "Q6"
            ).first()
            if existing_answer:
                existing_answer.answer = json.dumps(["Unknown"])
            else:
                db.add(PatientAnswer(
                    patient_id=input.patient_id,
                    question_id="Q6",
                    answer=json.dumps(["Unknown"])
                ))
            logger.info(f"Applied default 'Unknown' for Q6 in all_answers")
        db.commit()

        answered_ids = set(all_answers.keys())
        for question in questions:
            if question.id in answered_ids:
                continue
            if question.target_gender != "All" and question.target_gender != input.gender:
                continue
            if not is_age_in_range(input.age, question.target_age_range):
                continue
            dependencies = db.query(QuestionDependency).filter(QuestionDependency.question_id == question.id).all()
            if dependencies:
                satisfied = all(
                    any(dep.depends_on_answer == ans for ans in (
                        json.loads(all_answers[dep.depends_on_question_id])
                        if dep.depends_on_question_id in all_answers and isinstance(all_answers[dep.depends_on_question_id], str) and all_answers[dep.depends_on_question_id].startswith('[')
                        else all_answers.get(dep.depends_on_question_id, "").split(",")
                        if "," in str(all_answers.get(dep.depends_on_question_id, ""))
                        else [all_answers.get(dep.depends_on_question_id, "")]
                    ))
                    for dep in dependencies
                )
                if not satisfied:
                    continue
            return {
                "next_question": {
                    "id": question.id,
                    "text": question.text,
                    "type": question.type.lower().replace("checkbox", "multi_select"),
                    "options": question.options,
                    "required": question.required or (question.id == "Q4" and "Q3" in all_answers and all_answers["Q3"] == "Yes")
                }
            }
        return {"next_question": None}
    except Exception as e:
        logger.error(f"Error in /next-question: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/get-details/{patient_id}", response_model=PatientDetailsResponse)
def get_patient_details(patient_id: str, db: Session = Depends(get_db)):
    try:
        answers = db.query(PatientAnswer, Question).join(
            Question, PatientAnswer.question_id == Question.id
        ).filter(
            PatientAnswer.patient_id == patient_id
        ).order_by(Question.sequence).all()

        if not answers:
            raise HTTPException(status_code=404, detail=f"No details found for patient ID: {patient_id}")

        details = [
            PatientDetail(
                question_id=answer.question_id,
                text=question.text,
                type=question.type.lower().replace("checkbox", "multi_select"),
                answer=answer.answer
            )
            for answer, question in answers
        ]

        return {"patient_id": patient_id, "details": details}
    except Exception as e:
        logger.error(f"Error in /get-details/{patient_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/get-patient-ids", response_model=List[str])
def get_patient_ids(db: Session = Depends(get_db)):
    try:
        patient_ids = db.query(
            PatientAnswer.patient_id
        ).group_by(
            PatientAnswer.patient_id
        ).order_by(
            desc(func.max(PatientAnswer.created_at))
        ).all()
        result = [pid for pid, in patient_ids]
        logger.info(f"Returning patient IDs: {result}")
        return result
    except Exception as e:
        logger.error(f"Error in /get-patient-ids: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/", response_model=None)
async def get_form():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Cancer Risk Questionnaire</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body>
        <div class="container mt-5">
            <h1>Cancer Risk Questionnaire</h1>
            <div class="mb-4">
                <a href="/history" class="btn btn-info">View Patient History</a>
            </div>
            <!-- Questionnaire Form -->
            <form id="patientForm" class="mt-4">
                <div class="mb-3">
                    <label for="patientId" class="form-label">Patient ID</label>
                    <input type="text" class="form-control" id="patientId" required>
                </div>
                <div class="mb-3">
                    <label for="gender" class="form-label">Biological Sex</label>
                    <select class="form-select" id="gender" required>
                        <option value="Male">Male</option>
                        <option value="Female">Female</option>
                        <option value="Intersex">Intersex</option>
                    </select>
                </div>
                <div class="mb-3">
                    <label for="age" class="form-label">Age</label>
                    <input type="number" class="form-control" id="age" required min="0">
                </div>
                <div id="questionContainer" class="mb-3"></div>
                <button type="submit" class="btn btn-primary" id="nextQuestionBtn">Next Question</button>
                <button type="button" class="btn btn-warning d-none" id="prevQuestionBtn">Previous Question</button>
                <button type="button" class="btn btn-success d-none" id="nextPatientBtn">Next Patient</button>
            </form>
            <div id="error" class="alert alert-danger d-none mt-3" role="alert"></div>
        </div>
        <script>
            let previousAnswers = {};
            let currentQuestion = null;
            let questionHistory = []; // Tracks question IDs in order
            let questionCache = {}; // Cache question details

            // Populate patient IDs (for Next Patient updates)
            async function loadPatientIds() {
                try {
                    const response = await fetch('/get-patient-ids');
                    if (!response.ok) {
                        console.error('Failed to load patient IDs:', await response.text());
                    }
                } catch (error) {
                    console.error('Error loading patient IDs:', error);
                }
            }

            // Render question with pre-filled answer
            function renderQuestion(question, answer) {
                const questionContainer = document.getElementById('questionContainer');
                const isQ4Required = question.id === 'Q4' && previousAnswers['Q3'] === 'Yes';
                questionContainer.dataset.questionId = question.id;
                questionContainer.innerHTML = `
                    <label class="form-label">${question.text}${question.required || isQ4Required ? ' <span class="text-danger">*</span>' : ''}</label>
                    ${question.type === 'multi_select' ? 
                        question.options.map(opt => {
                            const isChecked = answer && answer.split(',').includes(opt) ? 'checked' : '';
                            return `
                                <div class="form-check">
                                    <input class="form-check-input" type="checkbox" name="answer" value="${opt}" ${isChecked} ${question.required || isQ4Required ? 'required' : ''}>
                                    <label class="form-check-label">${opt}</label>
                                </div>
                            `;
                        }).join('') :
                        question.type === 'text' || question.type === 'number' ?
                            `<input type="${question.type}" class="form-control" name="answer" value="${answer || ''}" ${question.required ? 'required' : ''}>` :
                            question.options.map(opt => {
                                const isChecked = answer === opt ? 'checked' : '';
                                return `
                                    <div class="form-check">
                                        <input class="form-check-input" type="radio" name="answer" value="${opt}" ${isChecked} ${question.required ? 'required' : ''}>
                                        <label class="form-check-label">${opt}</label>
                                    </div>
                                `;
                            }).join('')}
                `;
                // Dynamic required validation for radio/checkbox
                if ((question.required || isQ4Required) && question.type !== 'text' && question.type !== 'number') {
                    const inputs = questionContainer.querySelectorAll('input[name="answer"]');
                    inputs.forEach(input => {
                        input.addEventListener('change', () => {
                            const anyChecked = Array.from(inputs).some(inp => inp.checked);
                            inputs.forEach(inp => inp.required = !anyChecked);
                        });
                    });
                }
            }

            // Form submission
            document.getElementById('patientForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const errorDiv = document.getElementById('error');
                errorDiv.classList.add('d-none');

                const patientId = document.getElementById('patientId').value;
                const gender = document.getElementById('gender').value;
                const age = document.getElementById('age').value;

                // Validate patient ID and age
                if (!patientId.trim()) {
                    errorDiv.textContent = 'Please enter a valid Patient ID.';
                    errorDiv.classList.remove('d-none');
                    return;
                }
                if (!age || parseInt(age) < 0) {
                    errorDiv.textContent = 'Please enter a valid age.';
                    errorDiv.classList.remove('d-none');
                    return;
                }

                previousAnswers['Q1'] = gender;
                previousAnswers['Q2'] = age.toString();

                let currentAnswer = null;
                const answerInputs = document.querySelectorAll('input[name="answer"]:checked');
                if (answerInputs.length > 0) {
                    currentAnswer = Array.from(answerInputs).map(input => input.value).join(',');
                } else {
                    const textInput = document.querySelector('input[name="answer"]');
                    if (textInput && textInput.value.trim()) {
                        currentAnswer = textInput.value.trim();
                    } else if (currentQuestion && !currentQuestion.required && !(currentQuestion.id === 'Q4' && previousAnswers['Q3'] === 'Yes')) {
                        currentAnswer = '';
                        console.log(`Sending empty answer for optional question ${currentQuestion.id}`);
                    }
                }

                console.log('Current question:', currentQuestion);
                console.log('Current answer:', currentAnswer);

                // Validate required question or Q4 when Q3 = 'Yes'
                if (currentQuestion && (currentQuestion.required || (currentQuestion.id === 'Q4' && previousAnswers['Q3'] === 'Yes')) && (currentAnswer === null || currentAnswer === '')) {
                    errorDiv.textContent = `Please answer the question: "${currentQuestion.text}"`;
                    errorDiv.classList.remove('d-none');
                    console.log('Validation failed: Required question not answered.');
                    return;
                }

                const currentQuestionId = currentQuestion ? currentQuestion.id : null;
                if (currentQuestionId && (currentAnswer || currentAnswer === '') && currentQuestionId !== 'Q1' && currentQuestionId !== 'Q2') {
                    previousAnswers[currentQuestionId] = currentAnswer;
                    console.log(`Saved answer for ${currentQuestionId}: ${currentAnswer}`);
                }

                console.log('Sending previous_answers:', previousAnswers);

                try {
                    const response = await fetch('/next-question', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ patient_id: patientId, gender, age: parseInt(age), previous_answers: previousAnswers })
                    });

                    if (!response.ok) {
                        const errorText = await response.text();
                        errorDiv.textContent = `Error: ${errorText}`;
                        errorDiv.classList.remove('d-none');
                        console.error('API error:', errorText);
                        return;
                    }

                    const data = await response.json();
                    console.log('Next question response:', data);
                    const questionContainer = document.getElementById('questionContainer');
                    const nextQuestionBtn = document.getElementById('nextQuestionBtn');
                    const prevQuestionBtn = document.getElementById('prevQuestionBtn');
                    const nextPatientBtn = document.getElementById('nextPatientBtn');
                    questionContainer.innerHTML = '';
                    if (data.next_question && data.next_question.id !== 'Q1' && data.next_question.id !== 'Q2') {
                        currentQuestion = data.next_question;
                        questionCache[currentQuestion.id] = currentQuestion; // Cache question
                        questionHistory.push(currentQuestion.id); // Add to history
                        renderQuestion(currentQuestion, previousAnswers[currentQuestion.id]);
                        nextQuestionBtn.classList.remove('d-none');
                        prevQuestionBtn.classList.toggle('d-none', questionHistory.length <= 1);
                        nextPatientBtn.classList.add('d-none');
                    } else {
                        questionContainer.innerHTML = '<p>No more questions.</p>';
                        questionContainer.dataset.questionId = '';
                        nextQuestionBtn.classList.add('d-none');
                        prevQuestionBtn.classList.toggle('d-none', questionHistory.length <= 1);
                        nextPatientBtn.classList.remove('d-none');
                        currentQuestion = null;
                    }
                    await loadPatientIds();
                } catch (error) {
                    errorDiv.textContent = `Error fetching next question: ${error.message}`;
                    errorDiv.classList.remove('d-none');
                    console.error('Fetch error:', error);
                }
            });

            // Previous Question button
            document.getElementById('prevQuestionBtn').addEventListener('click', () => {
                const errorDiv = document.getElementById('error');
                errorDiv.classList.add('d-none');

                if (questionHistory.length > 1) {
                    questionHistory.pop(); // Remove current question
                    const prevQuestionId = questionHistory[questionHistory.length - 1];
                    const prevQuestion = questionCache[prevQuestionId];
                    if (prevQuestion) {
                        currentQuestion = prevQuestion;
                        renderQuestion(prevQuestion, previousAnswers[prevQuestionId]);
                        const questionContainer = document.getElementById('questionContainer');
                        questionContainer.dataset.questionId = prevQuestionId;
                        document.getElementById('prevQuestionBtn').classList.toggle('d-none', questionHistory.length <= 1);
                        document.getElementById('nextQuestionBtn').classList.remove('d-none');
                        document.getElementById('nextPatientBtn').classList.add('d-none');
                    }
                }
            });

            // Next Patient button
            document.getElementById('nextPatientBtn').addEventListener('click', async () => {
                previousAnswers = {};
                questionHistory = [];
                questionCache = {};
                document.getElementById('patientId').value = '';
                document.getElementById('gender').value = 'Male';
                document.getElementById('age').value = '';
                document.getElementById('questionContainer').innerHTML = '';
                document.getElementById('questionContainer').dataset.questionId = '';
                document.getElementById('nextQuestionBtn').classList.remove('d-none');
                document.getElementById('prevQuestionBtn').classList.add('d-none');
                document.getElementById('nextPatientBtn').classList.add('d-none');
                document.getElementById('error').classList.add('d-none');
                currentQuestion = null;
                await loadPatientIds();
            });

        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/history", response_model=None)
async def get_history():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Patient History</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body>
        <div class="container mt-5">
            <h1>Patient History</h1>
            <a href="/" class="btn btn-primary mb-4">Back to Questionnaire</a>
            <!-- Patient ID Dropdown -->
            <div class="mb-4">
                <label for="patientIdSelect" class="form-label">Select Patient ID</label>
                <select class="form-select" id="patientIdSelect">
                    <option value="">-- Select a Patient --</option>
                </select>
                <div id="noPatients" class="text-muted mt-2 d-none">No patients found.</div>
            </div>
            <!-- Patient Details -->
            <div id="patientDetails" class="mb-4 d-none">
                <h3>Patient Details</h3>
                <table class="table table-bordered">
                    <thead>
                        <tr>
                            <th>Question ID</th>
                            <th>Question Text</th>
                            <th>Type</th>
                            <th>Answer</th>
                        </tr>
                    </thead>
                    <tbody id="detailsTableBody"></tbody>
                </table>
            </div>
            <div id="error" class="alert alert-danger d-none mt-3" role="alert"></div>
        </div>
        <script>
            // Populate patient ID dropdown
            async function loadPatientIds() {
                try {
                    const response = await fetch('/get-patient-ids');
                    const select = document.getElementById('patientIdSelect');
                    const noPatientsDiv = document.getElementById('noPatients');
                    select.innerHTML = '<option value="">-- Select a Patient --</option>';
                    if (response.ok) {
                        const patientIds = await response.json();
                        if (patientIds.length === 0) {
                            noPatientsDiv.classList.remove('d-none');
                        } else {
                            noPatientsDiv.classList.add('d-none');
                            patientIds.forEach(id => {
                                const option = document.createElement('option');
                                option.value = id;
                                option.textContent = id;
                                select.appendChild(option);
                            });
                            console.log('Loaded patient IDs:', patientIds);
                        }
                    } else {
                        console.error('Failed to load patient IDs:', await response.text());
                        noPatientsDiv.classList.remove('d-none');
                    }
                } catch (error) {
                    console.error('Error loading patient IDs:', error);
                    document.getElementById('noPatients').classList.remove('d-none');
                }
            }

            // Load patient details
            async function loadPatientDetails(patientId) {
                const detailsDiv = document.getElementById('patientDetails');
                const tableBody = document.getElementById('detailsTableBody');
                const errorDiv = document.getElementById('error');
                tableBody.innerHTML = '';
                errorDiv.classList.add('d-none');
                if (!patientId) {
                    detailsDiv.classList.add('d-none');
                    return;
                }
                try {
                    const response = await fetch(`/get-details/${patientId}`);
                    if (response.ok) {
                        const data = await response.json();
                        data.details.forEach(detail => {
                            const row = document.createElement('tr');
                            row.innerHTML = `
                                <td>${detail.question_id}</td>
                                <td>${detail.text}</td>
                                <td>${detail.type}</td>
                                <td>${detail.answer}</td>
                            `;
                            tableBody.appendChild(row);
                        });
                        detailsDiv.classList.remove('d-none');
                    } else {
                        detailsDiv.classList.add('d-none');
                        errorDiv.textContent = `Error: ${await response.text()}`;
                        errorDiv.classList.remove('d-none');
                    }
                } catch (error) {
                    detailsDiv.classList.add('d-none');
                    errorDiv.textContent = `Error fetching details: ${error.message}`;
                    errorDiv.classList.remove('d-none');
                }
            }

            // Patient ID dropdown change
            document.getElementById('patientIdSelect').addEventListener('change', (e) => {
                loadPatientDetails(e.target.value);
            });

            // Initialize
            loadPatientIds();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# from fastapi import FastAPI, Depends, HTTPException
# from fastapi.responses import HTMLResponse
# from sqlalchemy.orm import Session
# from sqlalchemy.sql import text
# from pydantic import BaseModel
# from typing import Dict, List, Optional
# from models import Question, QuestionDependency, PatientAnswer
# from database import get_db, Base, engine, SessionLocal
# from csv_loader import load_csv_to_db
# import os
# import logging
# import json
# from sqlalchemy import desc, func

# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)

# app = FastAPI()

# @app.on_event("startup")
# def on_startup():
#     logger.info("Starting up and creating database tables...")
#     try:
#         Base.metadata.create_all(bind=engine)
#         logger.info(f"Tables registered with metadata: {Base.metadata.tables.keys()}")
#         with engine.connect() as conn:
#             result = conn.execute(text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'questions')"))
#             table_exists = result.scalar()
#             if table_exists:
#                 logger.info("Table 'questions' exists.")
#             else:
#                 logger.error("Table 'questions' was not created.")
#                 raise Exception("Failed to create 'questions' table.")
        
#         logger.info("Tables created successfully.")
#         csv_path = "Refined_Cancer_Risk_Questionnaire.csv"
#         if os.path.exists(csv_path):
#             with SessionLocal() as db:
#                 load_csv_to_db(db, csv_path)
#                 logger.info("CSV data loaded successfully.")
#         else:
#             logger.error(f"CSV file not found at {csv_path}")
#             raise FileNotFoundError(f"CSV file not found: {csv_path}")
#     except Exception as e:
#         logger.error(f"Error during startup: {e}")
#         raise

# # Pydantic models
# class PatientInput(BaseModel):
#     patient_id: str
#     gender: str
#     age: int
#     previous_answers: Dict[str, str]

# class QuestionResponse(BaseModel):
#     id: str
#     text: str
#     type: str
#     options: Optional[List[str]]
#     required: bool

# class NextQuestionResponse(BaseModel):
#     next_question: Optional[QuestionResponse]

# class PatientDetail(BaseModel):
#     question_id: str
#     text: str
#     type: str
#     answer: str

# class PatientDetailsResponse(BaseModel):
#     patient_id: str
#     details: List[PatientDetail]

# def is_age_in_range(age: int, age_range: str) -> bool:
#     if age_range == "Any":
#         return True
#     if "-" in age_range:
#         min_age, max_age = map(int, age_range.split("-"))
#         return min_age <= age <= max_age
#     if "+" in age_range:
#         min_age = int(age_range.replace("+", ""))
#         return age >= min_age
#     return False

# @app.post("/next-question", response_model=NextQuestionResponse)
# def get_next_question(input: PatientInput, db: Session = Depends(get_db)):
#     try:
#         valid_genders = ["Male", "Female", "Intersex"]
#         if input.gender not in valid_genders:
#             raise HTTPException(status_code=400, detail=f"Invalid gender. Must be one of {valid_genders}")
#         if input.age < 0:
#             raise HTTPException(status_code=400, detail="Age must be non-negative")

#         # Log incoming previous_answers
#         logger.info(f"Processing previous_answers for patient {input.patient_id}: {input.previous_answers}")

#         db_answers = db.query(PatientAnswer).filter(PatientAnswer.patient_id == input.patient_id).all()
#         existing_answers = {ans.question_id: ans.answer for ans in db_answers}

#         all_answers = existing_answers.copy()
#         all_answers.update(input.previous_answers)

#         all_answers["Q1"] = input.gender
#         all_answers["Q2"] = str(input.age)

#         for qid, answer in all_answers.items():
#             if not answer:  # Reject empty answers for required questions
#                 question = db.query(Question).filter(Question.id == qid).first()
#                 if question and question.required:
#                     logger.warning(f"Empty answer provided for required question {qid}")
#                     raise HTTPException(status_code=400, detail=f"Answer for required question {qid} cannot be empty")
#             existing_answer = db.query(PatientAnswer).filter(
#                 PatientAnswer.patient_id == input.patient_id,
#                 PatientAnswer.question_id == qid
#             ).first()
#             if existing_answer:
#                 existing_answer.answer = json.dumps(answer.split(",")) if "," in str(answer) else answer
#             else:
#                 db.add(PatientAnswer(
#                     patient_id=input.patient_id,
#                     question_id=qid,
#                     answer=json.dumps(answer.split(",")) if "," in str(answer) else answer
#                 ))
#         db.commit()

#         answered_ids = set(all_answers.keys())
#         questions = db.query(Question).order_by(Question.sequence).all()
        
#         for question in questions:
#             if question.id in answered_ids:
#                 continue
#             if question.target_gender != "All" and question.target_gender != input.gender:
#                 continue
#             if not is_age_in_range(input.age, question.target_age_range):
#                 continue
#             dependencies = db.query(QuestionDependency).filter(QuestionDependency.question_id == question.id).all()
#             if dependencies:
#                 satisfied = all(
#                     any(dep.depends_on_answer == ans for ans in (
#                         json.loads(all_answers[dep.depends_on_question_id])
#                         if dep.depends_on_question_id in all_answers and isinstance(all_answers[dep.depends_on_question_id], str) and all_answers[dep.depends_on_question_id].startswith('[')
#                         else all_answers.get(dep.depends_on_question_id, "").split(",")
#                         if "," in str(all_answers.get(dep.depends_on_question_id, ""))
#                         else [all_answers.get(dep.depends_on_question_id, "")]
#                     ))
#                     for dep in dependencies
#                 )
#                 if not satisfied:
#                     continue
#             return {
#                 "next_question": {
#                     "id": question.id,
#                     "text": question.text,
#                     "type": question.type.lower().replace("checkbox", "multi_select"),
#                     "options": question.options,
#                     "required": question.required
#                 }
#             }
#         return {"next_question": None}
#     except Exception as e:
#         logger.error(f"Error in /next-question: {e}")
#         raise HTTPException(status_code=500, detail=str(e))

# @app.get("/get-details/{patient_id}", response_model=PatientDetailsResponse)
# def get_patient_details(patient_id: str, db: Session = Depends(get_db)):
#     try:
#         answers = db.query(PatientAnswer, Question).join(
#             Question, PatientAnswer.question_id == Question.id
#         ).filter(
#             PatientAnswer.patient_id == patient_id
#         ).order_by(Question.sequence).all()

#         if not answers:
#             raise HTTPException(status_code=404, detail=f"No details found for patient ID: {patient_id}")

#         details = [
#             PatientDetail(
#                 question_id=answer.question_id,
#                 text=question.text,
#                 type=question.type.lower().replace("checkbox", "multi_select"),
#                 answer=answer.answer
#             )
#             for answer, question in answers
#         ]

#         return {"patient_id": patient_id, "details": details}
#     except Exception as e:
#         logger.error(f"Error in /get-details/{patient_id}: {e}")
#         raise HTTPException(status_code=500, detail=str(e))

# @app.get("/get-patient-ids", response_model=List[str])
# def get_patient_ids(db: Session = Depends(get_db)):
#     try:
#         patient_ids = db.query(
#             PatientAnswer.patient_id
#         ).group_by(
#             PatientAnswer.patient_id
#         ).order_by(
#             desc(func.max(PatientAnswer.created_at))
#         ).all()
#         result = [pid for pid, in patient_ids]
#         logger.info(f"Returning patient IDs: {result}")
#         return result
#     except Exception as e:
#         logger.error(f"Error in /get-patient-ids: {e}")
#         raise HTTPException(status_code=500, detail=str(e))

# @app.get("/", response_model=None)
# async def get_form():
#     html_content = """
#     <!DOCTYPE html>
#     <html lang="en">
#     <head>
#         <meta charset="UTF-8">
#         <meta name="viewport" content="width=device-width, initial-scale=1.0">
#         <title>Cancer Risk Questionnaire</title>
#         <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
#     </head>
#     <body>
#         <div class="container mt-5">
#             <h1>Cancer Risk Questionnaire</h1>
#             <!-- Patient ID Dropdown -->
#             <div class="mb-4">
#                 <label for="patientIdSelect" class="form-label">Select Patient ID</label>
#                 <select class="form-select" id="patientIdSelect">
#                     <option value="">-- Select a Patient --</option>
#                 </select>
#                 <div id="noPatients" class="text-muted mt-2 d-none">No patients found.</div>
#             </div>
#             <!-- Patient Details -->
#             <div id="patientDetails" class="mb-4 d-none">
#                 <h3>Patient Details</h3>
#                 <table class="table table-bordered">
#                     <thead>
#                         <tr>
#                             <th>Question ID</th>
#                             <th>Question Text</th>
#                             <th>Type</th>
#                             <th>Answer</th>
#                         </tr>
#                     </thead>
#                     <tbody id="detailsTableBody"></tbody>
#                 </table>
#             </div>
#             <!-- Questionnaire Form -->
#             <form id="patientForm" class="mt-4">
#                 <div class="mb-3">
#                     <label for="patientId" class="form-label">Patient ID</label>
#                     <input type="text" class="form-control" id="patientId" required>
#                 </div>
#                 <div class="mb-3">
#                     <label for="gender" class="form-label">Biological Sex</label>
#                     <select class="form-select" id="gender" required>
#                         <option value="Male">Male</option>
#                         <option value="Female">Female</option>
#                         <option value="Intersex">Intersex</option>
#                     </select>
#                 </div>
#                 <div class="mb-3">
#                     <label for="age" class="form-label">Age</label>
#                     <input type="number" class="form-control" id="age" required min="0">
#                 </div>
#                 <div id="questionContainer" class="mb-3"></div>
#                 <button type="submit" class="btn btn-primary" id="nextQuestionBtn">Next Question</button>
#                 <button type="button" class="btn btn-success d-none" id="nextPatientBtn">Next Patient</button>
#             </form>
#             <div id="error" class="alert alert-danger d-none mt-3" role="alert"></div>
#         </div>
#         <script>
#             let previousAnswers = {};
#             let currentQuestion = null;

#             // Populate patient ID dropdown
#             async function loadPatientIds() {
#                 try {
#                     const response = await fetch('/get-patient-ids');
#                     const select = document.getElementById('patientIdSelect');
#                     const noPatientsDiv = document.getElementById('noPatients');
#                     select.innerHTML = '<option value="">-- Select a Patient --</option>';
#                     if (response.ok) {
#                         const patientIds = await response.json();
#                         if (patientIds.length === 0) {
#                             noPatientsDiv.classList.remove('d-none');
#                         } else {
#                             noPatientsDiv.classList.add('d-none');
#                             patientIds.forEach(id => {
#                                 const option = document.createElement('option');
#                                 option.value = id;
#                                 option.textContent = id;
#                                 select.appendChild(option);
#                             });
#                             console.log('Loaded patient IDs:', patientIds);
#                         }
#                     } else {
#                         console.error('Failed to load patient IDs:', await response.text());
#                         noPatientsDiv.classList.remove('d-none');
#                     }
#                 } catch (error) {
#                     console.error('Error loading patient IDs:', error);
#                     document.getElementById('noPatients').classList.remove('d-none');
#                 }
#             }

#             // Load patient details
#             async function loadPatientDetails(patientId) {
#                 const detailsDiv = document.getElementById('patientDetails');
#                 const tableBody = document.getElementById('detailsTableBody');
#                 tableBody.innerHTML = '';
#                 if (!patientId) {
#                     detailsDiv.classList.add('d-none');
#                     return;
#                 }
#                 try {
#                     const response = await fetch(`/get-details/${patientId}`);
#                     if (response.ok) {
#                         const data = await response.json();
#                         data.details.forEach(detail => {
#                             const row = document.createElement('tr');
#                             row.innerHTML = `
#                                 <td>${detail.question_id}</td>
#                                 <td>${detail.text}</td>
#                                 <td>${detail.type}</td>
#                                 <td>${detail.answer}</td>
#                             `;
#                             tableBody.appendChild(row);
#                         });
#                         detailsDiv.classList.remove('d-none');
#                     } else {
#                         detailsDiv.classList.add('d-none');
#                         alert(`Error: ${await response.text()}`);
#                     }
#                 } catch (error) {
#                     detailsDiv.classList.add('d-none');
#                     alert(`Error fetching details: ${error.message}`);
#                 }
#             }

#             // Form submission
#             document.getElementById('patientForm').addEventListener('submit', async (e) => {
#                 e.preventDefault();
#                 const errorDiv = document.getElementById('error');
#                 errorDiv.classList.add('d-none');

#                 const patientId = document.getElementById('patientId').value;
#                 const gender = document.getElementById('gender').value;
#                 const age = parseInt(document.getElementById('age').value);

#                 previousAnswers['Q1'] = gender;
#                 previousAnswers['Q2'] = age.toString();

#                 let currentAnswer = null;
#                 const answerInputs = document.querySelectorAll('input[name="answer"]:checked');
#                 if (answerInputs.length > 0) {
#                     currentAnswer = Array.from(answerInputs).map(input => input.value).join(',');
#                 } else {
#                     const textInput = document.querySelector('input[name="answer"]');
#                     if (textInput && textInput.value.trim()) {
#                         currentAnswer = textInput.value.trim();
#                     }
#                 }

#                 console.log('Current question:', currentQuestion);
#                 console.log('Current answer:', currentAnswer);

#                 // Validate required question
#                 if (currentQuestion && currentQuestion.required && (currentAnswer === null || currentAnswer === '')) {
#                     errorDiv.textContent = 'Please answer this question before proceeding.';
#                     errorDiv.classList.remove('d-none');
#                     console.log('Validation failed: Required question not answered.');
#                     return;
#                 }

#                 const currentQuestionId = currentQuestion ? currentQuestion.id : null;
#                 if (currentQuestionId && currentAnswer && currentQuestionId !== 'Q1' && currentQuestionId !== 'Q2') {
#                     previousAnswers[currentQuestionId] = currentAnswer;
#                     console.log(`Saved answer for ${currentQuestionId}: ${currentAnswer}`);
#                 }

#                 try {
#                     const response = await fetch('/next-question', {
#                         method: 'POST',
#                         headers: { 'Content-Type': 'application/json' },
#                         body: JSON.stringify({ patient_id: patientId, gender, age, previous_answers: previousAnswers })
#                     });

#                     if (!response.ok) {
#                         errorDiv.textContent = `Error: ${await response.text()}`;
#                         errorDiv.classList.remove('d-none');
#                         console.error('API error:', await response.text());
#                         return;
#                     }

#                     const data = await response.json();
#                     console.log('Next question response:', data);
#                     const questionContainer = document.getElementById('questionContainer');
#                     const nextQuestionBtn = document.getElementById('nextQuestionBtn');
#                     const nextPatientBtn = document.getElementById('nextPatientBtn');
#                     questionContainer.innerHTML = '';
#                     if (data.next_question && data.next_question.id !== 'Q1' && data.next_question.id !== 'Q2') {
#                         currentQuestion = data.next_question;
#                         questionContainer.dataset.questionId = data.next_question.id;
#                         questionContainer.innerHTML = `
#                             <label class="form-label">${data.next_question.text}${data.next_question.required ? ' <span class="text-danger">*</span>' : ''}</label>
#                             ${data.next_question.type === 'multi_select' ? 
#                                 data.next_question.options.map(opt => `
#                                     <div class="form-check">
#                                         <input class="form-check-input" type="checkbox" name="answer" value="${opt}">
#                                         <label class="form-check-label">${opt}</label>
#                                     </div>
#                                 `).join('') :
#                                 data.next_question.type === 'text' || data.next_question.type === 'number' ?
#                                     `<input type="${data.next_question.type}" class="form-control" name="answer">` :
#                                     data.next_question.options.map(opt => `
#                                         <div class="form-check">
#                                             <input class="form-check-input" type="radio" name="answer" value="${opt}">
#                                             <label class="form-check-label">${opt}</label>
#                                         </div>
#                                     `).join('')}
#                         `;
#                         nextQuestionBtn.classList.remove('d-none');
#                         nextPatientBtn.classList.add('d-none');
#                     } else {
#                         questionContainer.innerHTML = '<p>No more questions.</p>';
#                         questionContainer.dataset.questionId = '';
#                         nextQuestionBtn.classList.add('d-none');
#                         nextPatientBtn.classList.remove('d-none');
#                         currentQuestion = null;
#                     }
#                     await loadPatientIds();
#                 } catch (error) {
#                     errorDiv.textContent = `Error fetching next question: ${error.message}`;
#                     errorDiv.classList.remove('d-none');
#                     console.error('Fetch error:', error);
#                 }
#             });

#             // Next Patient button
#             document.getElementById('nextPatientBtn').addEventListener('click', async () => {
#                 previousAnswers = {};
#                 document.getElementById('patientId').value = '';
#                 document.getElementById('gender').value = 'Male';
#                 document.getElementById('age').value = '';
#                 document.getElementById('questionContainer').innerHTML = '';
#                 document.getElementById('questionContainer').dataset.questionId = '';
#                 document.getElementById('nextQuestionBtn').classList.remove('d-none');
#                 document.getElementById('nextPatientBtn').classList.add('d-none');
#                 document.getElementById('error').classList.add('d-none');
#                 currentQuestion = null;
#                 await loadPatientIds();
#             });

#             // Patient ID dropdown change
#             document.getElementById('patientIdSelect').addEventListener('change', (e) => {
#                 loadPatientDetails(e.target.value);
#             });

#             // Initialize
#             loadPatientIds();
#         </script>
#     </body>
#     </html>
#     """
#     return HTMLResponse(content=html_content)