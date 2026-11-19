"""Open the built docx in Word, repaginate, report pages, export a PDF."""
import pathlib
import sys

import win32com.client

DOC = pathlib.Path(__file__).parent / "Sushi - Features and Proposals.docx"
PDF = DOC.with_suffix(".pdf")

word = win32com.client.Dispatch("Word.Application")
word.Visible = False
try:
    doc = word.Documents.Open(str(DOC), ReadOnly=False)
    doc.Repaginate()
    pages = doc.ComputeStatistics(2)  # wdStatisticPages
    print("pages:", pages)
    for i in range(1, pages + 1):
        rng = doc.GoTo(1, 1, i)  # wdGoToPage, wdGoToAbsolute
        text = rng.Paragraphs(1).Range.Text.strip()
        print(f"  p{i} starts: {text[:70]!r}")
    doc.SaveAs(str(PDF), FileFormat=17)  # wdFormatPDF
    doc.Close(False)
finally:
    word.Quit()
print("pdf:", PDF)
