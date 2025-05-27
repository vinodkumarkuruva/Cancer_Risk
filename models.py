from sqlalchemy import Column, Integer, String, Boolean, JSON, Enum, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base

class Question(Base):
    __tablename__ = "questions"
    id = Column(String, primary_key=True)
    sequence = Column(Integer, nullable=False)
    section = Column(String, nullable=False)
    sub_section = Column(String, nullable=False)
    text = Column(String, nullable=False)
    type = Column(Enum("Radio", "Checkbox", "Number", "Text", name="question_type"), nullable=False)
    options = Column(JSON, nullable=True)
    required = Column(Boolean, nullable=False)
    target_gender = Column(String, nullable=True)
    target_age_range = Column(String, nullable=True)
    info_tooltip = Column(String, nullable=True)
    dependencies = relationship("QuestionDependency", back_populates="question")

class QuestionDependency(Base):
    __tablename__ = "question_dependencies"
    id = Column(Integer, primary_key=True)
    question_id = Column(String, ForeignKey("questions.id"), nullable=False)
    depends_on_question_id = Column(String, nullable=False)
    depends_on_answer = Column(String, nullable=False)
    question = relationship("Question", back_populates="dependencies")

class PatientAnswer(Base):
    __tablename__ = "patient_answers"
    id = Column(Integer, primary_key=True)
    patient_id = Column(String, nullable=False)
    question_id = Column(String, ForeignKey("questions.id"), nullable=False)
    answer = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    question = relationship("Question")