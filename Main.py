import streamlit as st
import requests
from datetime import datetime

# ==========================================
# CONFIGURAÇÕES
# ==========================================
st.set_page_config(page_title="ERP Ativos Cobli", layout="wide")

URL_N8N = "https://n8n.efop.cobli.co/webhook/gestao-ativos"

@st.cache_data(ttl=3600)
def buscar_slack():
    try:
        res = requests.get(URL_N8N, params={"action": "buscar-colab"}, timeout=20)
        if res.status_code == 200: return res.json().get("dados", [])
        return []
    except: return []

@st.cache_data(ttl=30)
def buscar_planilhas(acao):
    try:
        res = requests.get(URL_N8N, params={"action": acao}, timeout=15)
        if res.status_code == 200: return res.json().get("dados", [])
        return []
    except: return []

# ==========================================
# CARREGAMENTO DE DADOS BLINDADO
# ==========================================
st.title("🏢 Gestão de Ativos - Cobli")

vigentes = buscar_planilhas("buscar-vigentes")
storage = buscar_planilhas("buscar-storage")

dados_slack = buscar_slack()
if dados_slack:
    nomes = sorted([c["nome"] for c in dados_slack])
else:
    nomes_extraidos = list(set([str(linha.get("Colaborador", "")).strip() for linha in vigentes if linha.get("Colaborador")]))
    nomes = sorted([n for n in nomes_extraidos if "DEVOLVIDO" not in n.upper()])
    if not nomes: nomes = ["(Aguardando desbloqueio do Slack...)"]

# ==========================================
# DASHBOARD LATERAL E CONTAGEM
# ==========================================
ativos_colab = []
em_uso = {"Notebook": 0, "Monitor": 0, "Celular": 0, "Headset": 0, "Teclado/Mouse": 0}
estoque = {"Notebook": 0, "Monitor": 0, "Celular": 0, "Headset": 0, "Teclado/Mouse": 0}
danificados = 0

with st.sidebar:
    if st.button("🔄 Atualizar Planilhas"):
        buscar_planilhas.clear() 
        st.rerun()
    
    st.markdown("### 👤 Selecionar Colaborador")
    colab_sel = st.selectbox("Colaborador atual:", nomes)
    
    for linha in vigentes:
        colab = str(linha.get("Colaborador", "")).strip()
        eqp = str(linha.get("Equipamento", "")).strip()
        cobli = str(linha.get("Cobli") or linha.get("Cobli_Novo") or "").strip()
        
        if colab and "DEVOLVIDO" not in colab.upper() and eqp:
            if eqp not in em_uso: em_uso[eqp] = 0
            em_uso[eqp] += 1
            
        if colab.upper() == colab_sel.upper() and eqp:
            ativos_colab.append({"eqp": eqp, "cobli": cobli})

    for linha in storage:
        eqp = str(linha.get("Equipamento", "")).strip()
        cond = str(linha.get("Condicao", "")).strip().upper()
        
        if cond in ["DEFEITO", "AVARIADO"]:
            danificados += 1
        elif eqp:
            if eqp not in estoque: estoque[eqp] = 0
            estoque[eqp] += 1

    st.markdown("---")
    st.markdown("### 📊 Resumo")
    col_a, col_b = st.columns(2)
    col_a.metric("✅ Em Uso", sum(em_uso.values()))
    col_b.metric("📦 Estoque", sum(estoque.values()))
    st.metric("⚠️ Danificados", danificados)

# ==========================================
# CORPO PRINCIPAL (SISTEMA DE ABAS)
# ==========================================
tab_mov, tab_lista = st.tabs(["🚀 Movimentação de Ativos", "📋 Lista Geral de Colaboradores"])

# ---------------- ABA 1: MOVIMENTAÇÃO ----------------
with tab_mov:
    fluxo = st.radio("Operação:", ["Onboarding", "Troca", "Emprestimo", "Devolvido", "Offboarding"], horizontal=True)
    st.markdown("---")

    if fluxo == "Offboarding":
        opcoes = [f"{a['eqp']} | {a['cobli']}" for a in ativos_colab]
        selecionar_tudo = st.checkbox("Selecionar TODOS para devolução", value=True)
        eqps_finais = st.multiselect("Itens a devolver:", opcoes, default=opcoes if selecionar_tudo else [])
    else:
        lista_eqp = ["Notebook", "Monitor", "Celular", "Headset", "Teclado/Mouse"]
        for a in ativos_colab:
            if a["eqp"] not in lista_eqp: lista_eqp.append(a["eqp"])
        eqp_sel = st.selectbox("Equipamento:", lista_eqp)

    cobli_sug = ""
    if fluxo in ["Troca", "Devolvido"]:
        for a in ativos_colab:
            if a["eqp"].upper() == eqp_sel.upper():
                cobli_sug = a["cobli"]
                break

    with st.form("form_master"):
        c_ant, c_nov, cond = "", "", "N/A"
        
        if fluxo == "Onboarding":
            c_nov = st.text_input("Nº Cobli Novo:")
        elif fluxo == "Emprestimo":
            c_nov = st.text_input("Nº Cobli Novo:")
            prazo = st.date_input("Data de Retorno Prevista:", datetime.now()).strftime("%d/%m/%Y")
        elif fluxo == "Devolvido":
            c_ant = st.text_input("Cobli a Devolver (Automático):", value=cobli_sug)
            cond = st.selectbox("Condição do Item:", ["Perfeito", "Defeito", "Avariado"])
        elif fluxo == "Troca":
            ca, cb = st.columns(2)
            c_ant = ca.text_input("Cobli Antigo (Automático):", value=cobli_sug)
            c_nov = cb.text_input("Cobli Novo:")
            cond = st.selectbox("Condição do Item Antigo:", ["Perfeito", "Defeito", "Avariado"])
        
        obs = st.text_area("Observações:")
        
        if st.form_submit_button("🚀 REGISTRAR MOVIMENTAÇÃO", type="primary"):
            user = next((c for c in dados_slack if c["nome"] == colab_sel), None) if dados_slack else None
            slack_id = user["id"] if user else ""
            data_str = datetime.now().strftime("%d/%m/%Y %H:%M")
            
            with st.spinner("Enviando para o n8n..."):
                if fluxo == "Offboarding":
                    if not eqps_finais: st.error("Nenhum item selecionado.")
                    else:
                        for item in eqps_finais:
                            partes = item.split(" | ")
                            requests.post(URL_N8N, json={"action": "app-post", "colaborador": colab_sel, "slack_id": slack_id, "equipamento": partes[0].strip(), "acao": "Offboarding", "cobli_antigo": partes[1].strip() if len(partes)>1 else "", "cobli_novo": "", "prazo": "Definitivo", "condicao": cond, "observacao": obs, "data": data_str})
                        st.success("✅ Offboarding Processado!")
                else:
                    p_final = prazo if fluxo == "Emprestimo" else "Definitivo"
                    res = requests.post(URL_N8N, json={"action": "app-post", "colaborador": colab_sel, "slack_id": slack_id, "equipamento": eqp_sel, "acao": fluxo, "cobli_antigo": c_ant, "cobli_novo": c_nov, "prazo": p_final, "condicao": cond, "observacao": obs, "data": data_str})
                    if res.status_code == 200: st.success("✅ Protocolo Registrado!")
                    else: st.error("❌ Falha no envio.")

# ---------------- ABA 2: LISTA GERAL ----------------
with tab_lista:
    st.markdown("### 📋 Visão Geral de Equipamentos por Colaborador")
    
    pessoas_eqp = {}
    for linha in vigentes:
        colab = str(linha.get("Colaborador", "")).strip()
        eqp = str(linha.get("Equipamento", "")).strip()
        cobli = str(linha.get("Cobli") or linha.get("Cobli_Novo") or "").strip()
        
        if colab and "DEVOLVIDO" not in colab.upper() and eqp:
            if colab not in pessoas_eqp: pessoas_eqp[colab] = []
            pessoas_eqp[colab].append(f"**{eqp}** ({cobli})")
            
    if not pessoas_eqp:
        st.info("Nenhum equipamento em posse no momento.")
    else:
        for pessoa in sorted(pessoas_eqp.keys()):
            with st.expander(f"👤 {pessoa} - {len(pessoas_eqp[pessoa])} item(ns)"):
                for item in pessoas_eqp[pessoa]:
                    st.write(f"- {item}")