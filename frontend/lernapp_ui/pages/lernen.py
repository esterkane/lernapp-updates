"""A single starting point for short learning activities."""
import streamlit as st

st.title("🎯 Lernen")
st.write("Wähle, was du heute üben möchtest.")
activities = [
    ("Vokabeln", "Wörter wiederholen und neue Lernkarten aus Wortlisten hinzufügen.", "vokabeln.py", "📚"),
    ("Sprechen", "Eine Antwort aufnehmen und Rückmeldung bekommen.", "sprechen.py", "🎤"),
    ("Schreiben", "Einen Text verfassen und Schritt für Schritt verbessern.", "schreiben.py", "✍️"),
    ("Lesen & Hören", "Kurze Texte und Hörübungen bearbeiten.", "lesen_hoeren.py", "📖"),
    ("Fragen stellen", "Grammatik erklären lassen und Gespräche führen.", "ueben.py", "💬"),
    ("Verhandeln", "Ein Gespräch aus dem Berufsalltag durchspielen.", "verhandeln.py", "🤝"),
]
columns = st.columns(2)
for index, (title, description, route, icon) in enumerate(activities):
    with columns[index % 2].container(border=True):
        st.subheader(f"{icon} {title}")
        st.write(description)
        if st.button(f"{title} starten", key=route, width="stretch"):
            st.switch_page(f"pages/{route}")
st.caption("Für Aufgaben aus deinen PDFs und vollständige Prüfungen öffne „Modelltests“.")
