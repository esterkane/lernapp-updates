"""Provider billing only; local estimates and hypothetical cloud costs are not displayed."""

from __future__ import annotations

import streamlit as st
from lernapp_ui.billing_ui import openai_billing

st.title("Abrechnung")
st.caption("Prüfe deine Abrechnung direkt beim Anbieter.")

openai_billing()
