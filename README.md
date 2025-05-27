# Title -  Cancer Risk Questionnaire

   A web-based application built with FastAPI to administer a cancer risk assessment questionnaire, store patient responses in a PostgreSQL database, and allow doctors to view patient history. The application supports dynamic question navigation, mandatory question enforcement, default answers for specific questions, and a history page for reviewing patient details.


## Features : 

- **Dynamic Questionnaire:** Presents questions based on patient gender, age, and previous answers, with conditional logic. 

- **Patient History Page:** Displays patient responses in a table, accessible via a separate /history page with a patient ID dropdown.

- **PostgreSQL Database: ** Stores questions (loaded from a CSV) and patient answers. 

- **Bootstrap UI:** Responsive and user-friendly interface with Bootstrap 5.3. 

- **Deployable:** Configured for deployment on Render with Uvicorn.

## Project Structure :

cancer-risk-questionnaire/
├── main.py                   # FastAPI application with endpoints and UI
├── models.py                # SQLAlchemy models for questions and answers
├── database.py              # Database configuration and session management
├── csv_loader.py            # Loads questionnaire data from CSV
├── Refined_Cancer_Risk_Questionnaire.csv  # Questionnaire data
├── requirements.txt         # Python dependencies

## Prerequisites :

- **Python   :**  3.8 or higher

- **Frontend :**  HTML, CSS, Bootstrap,Javascript

- **Database:**   PostgreSQL

- **Render Account:** For deployment (optional)

## Installation & Setup : 

1. Clone the Repository -
```
   git clone [https://github.com/vinodkumarkuruva/Cancer_Risk.git]
   cd Cancer_Risk
```

2. Set Up a Virtual Environment (Optional but Recommended) -

```
python -m venv env
env\Scripts\activate         # On Windows
```

3. Install Required Dependencies -
   
```
pip install -r requirements.txt
```

4. Configure the Database -
    
```
For PostgreSQL:
Update the DATABASES setting[URL] in .env file.
 ```

5. Run the Server -

```
uvicorn main:app --reload
Access the application at : http://127.0.0.1:8000/
```

