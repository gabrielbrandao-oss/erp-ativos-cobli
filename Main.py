# app.py
import streamlit as st
import requests
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==========================================
# CONFIGURAÇÕES E SEGURANÇA
# ==========================================
st.set_page_config(page_title="Periféricos Cobli", layout="wide")

# Fallback seguro para variáveis de ambiente/secrets (Evita hardcode de URLs e Tokens)
WEBHOOK_URL = st.secrets.get("N8N_WEBHOOK_URL", "https://n8n.efop.cobli.co/webhook/gestao-ativos")
API_KEY = st.secrets.get("API_KEY", "")

# Configuração de sessão HTTP resiliente
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))
session.headers.update({"Authorization": f"Bearer {API_KEY}"} if API_KEY else {})

# ==========================================
# CAMADA DE INTEGRAÇÃO (API)
# ==========================================
@st.cache_data(ttl=3600, show_spinner=False)
def buscar_slack() -> List[Dict[str, Any]]:
    try:
        res = session.get(WEBHOOK_URL, params={"action": "buscar-colab"}, timeout=10)
        res.raise_for_status()
        return res.json().get("dados", [])
    except requests.exceptions.RequestException as e:
        st.error("Falha na comunicação com o serviço de diretório (Slack).")
        return []

@st.cache_data(ttl=30, show_spinner=False)
def buscar_planilhas(acao: str) -> List[Dict[str, Any]]:
    try:
        res = session.get(WEBHOOK_URL, params={"action": acao}, timeout=10)
        res.raise_for_status()
        return res.json().get("dados", [])
    except requests.exceptions.RequestException as e:
        st.error(f"Falha ao buscar dados da base de ativos: {acao}.")
        return []

def enviar_movimentacao(payload: Dict[str, Any]) -> bool:
    try:
        res = session.post(WEBHOOK_URL, json=payload, timeout=15)
        res.raise_for_status()
        return True
    except requests.exceptions.RequestException:
        return False

# ==========================================
# CAMADA DE VALIDAÇÃO (SECURITY BY DESIGN)
# ==========================================
def sanitizar_input(texto: str) -> str:
    """Remove caracteres perigosos para mitigar injeção de payload."""
    if not texto:
        return ""
    return re.sub(r'[<>{}\[\]]', '', str(texto)).strip()

def processar_offboarding(eqps_finais: List[str], colab_sel: str, slack_id: str, cond: str, obs: str, data_str: str) -> bool:
    sucesso_total = True
    for item in eqps_finais:
        partes = item.split(" | ")
        payload = {
            "action": "app-post",
            "colaborador": colab_sel,
            "slack_id": slack_id,
            "equipamento": partes[0].strip(),
            "acao": "Offboarding",
            "cobli_antigo": partes[1].strip() if len(partes) > 1 else "",
            "cobli_novo": "",
            "prazo": "Definitivo",
            "condicao": cond,
            "observacao": obs,
            "data": data_str
        }
        if not enviar_movimentacao(payload):
            sucesso_total = False
    return sucesso_total

# ==========================================
# CONTROLADOR / UI
# ==========================================
def main():
    st.title("🏢 Gestão de Ativos - Cobli")

    vigentes = buscar_planilhas("buscar-vigentes")
    storage = buscar_planilhas("buscar-storage")
    dados_slack = buscar_slack()

    if dados_slack:
        nomes = sorted([c.get("nome", "Desconhecido") for c in dados_slack])
    else:
        nomes_extraidos = {str(linha.get("Colaborador", "")).strip() for linha in vigentes if linha.get("Colaborador")}
        nomes = sorted([n for n in nomes_extraidos if "DEVOLVIDO" not in n.upper()])
        if not nomes: 
            nomes = ["(Aguardando desbloqueio do Slack...)"]

    # --- PROCESSAMENTO DE ESTOQUE ---
    ativos_colab = []
    chaves_eqp = ["Notebook", "Monitor", "Celular", "Headset", "Teclado/Mouse"]
    em_uso = {k: 0 for k in chaves_eqp}
    estoque = {k: 0 for k in chaves_eqp}
    danificados = 0

    with st.sidebar:
        if st.button("🔄 Atualizar Planilhas", use_container_width=True):
            buscar_planilhas.clear()
            st.rerun()
        
        st.markdown("### 👤 Selecionar Colaborador")
        colab_sel = st.selectbox("Colaborador atual:", nomes)
        
        for linha in vigentes:
            colab = str(linha.get("Colaborador", "")).strip()
            eqp = str(linha.get("Equipamento", "")).strip()
            cobli = str(linha.get("Cobli") or linha.get("Cobli_Novo") or "").strip()
            
            if colab and "DEVOLVIDO" not in colab.upper() and "EXTRAVIADO" not in colab.upper() and eqp:
                em_uso[eqp] = em_uso.get(eqp, 0) + 1
                if colab.upper() == colab_sel.upper():
                    ativos_colab.append({"eqp": eqp, "cobli": cobli})

        for linha in storage:
            eqp = str(linha.get("Equipamento", "")).strip()
            cond = str(linha.get("Condicao", "")).strip().upper()
            if cond in ["DEFEITO", "AVARIADO"]: 
                danificados += 1
            elif eqp:
                estoque[eqp] = estoque.get(eqp, 0) + 1

        st.markdown("---")
        st.markdown("### 📊 Resumo")
        col_a, col_b = st.columns(2)
        col_a.metric("✅ Em Uso", sum(em_uso.values()))
        col_b.metric("📦 Estoque", sum(estoque.values()))
        st.metric("⚠️ Danificados", danificados)

    # --- ABAS DE INTERFACE ---
    tab_mov, tab_lista = st.tabs(["🚀 Movimentação", "📋 Lista Geral"])

    with tab_mov:
        fluxo = st.radio("Operação:", ["Onboarding", "Troca", "Emprestimo", "Devolvido", "Offboarding", "Extravio"], horizontal=True)
        st.markdown("---")

        eqps_finais = []
        eqp_sel = ""
        cobli_sug = ""

        if fluxo == "Offboarding":
            opcoes = [f"{a['eqp']} | {a['cobli']}" for a in ativos_colab]
            selecionar_tudo = st.checkbox("Selecionar TODOS para devolução", value=True)
            eqps_finais = st.multiselect("Itens a devolver:", opcoes, default=opcoes if selecionar_tudo else [])
        else:
            lista_eqp = chaves_eqp.copy()
            for a in ativos_colab:
                if a["eqp"] not in lista_eqp: 
                    lista_eqp.append(a["eqp"])
            eqp_sel = st.selectbox("Equipamento:", lista_eqp)
            
            if fluxo in ["Troca", "Devolvido", "Extravio"]:
                for a in ativos_colab:
                    if a["eqp"].upper() == eqp_sel.upper():
                        cobli_sug = a["cobli"]
                        break

        with st.form("form_master", clear_on_submit=False):
            c_ant, c_nov, cond = "", "", "N/A"
            prazo_str = "Definitivo"
            
            if fluxo == "Onboarding":
                c_nov = st.text_input("Nº Cobli Novo:")
            elif fluxo == "Emprestimo":
                c_nov = st.text_input("Nº Cobli Novo:")
                prazo = st.date_input("Data de Retorno Prevista:", datetime.now())
                prazo_str = prazo.strftime("%d/%m/%Y")
            elif fluxo == "Devolvido":
                c_ant = st.text_input("Cobli a Devolver (Automático):", value=cobli_sug)
                cond = st.selectbox("Condição do Item:", ["Perfeito", "Defeito", "Avariado"])
            elif fluxo == "Troca":
                ca, cb = st.columns(2)
                c_ant = ca.text_input("Cobli Antigo (Automático):", value=cobli_sug)
                c_nov = cb.text_input("Cobli Novo:")
                cond = st.selectbox("Condição do Item Antigo:", ["Perfeito", "Defeito", "Avariado"])
            elif fluxo == "Extravio":
                c_ant = st.text_input("Cobli Extraviado (Automático):", value=cobli_sug)
                cond = st.selectbox("Motivo:", ["Roubo", "Perda", "Dano Total"])
            
            obs = st.text_area("Observações:")
            submit = st.form_submit_button("🚀 REGISTRAR MOVIMENTAÇÃO", type="primary")
            
            if submit:
                # Sanitização (AppSec)
                c_ant = sanitizar_input(c_ant)
                c_nov = sanitizar_input(c_nov)
                obs_sanitizada = sanitizar_input(obs)

                user = next((c for c in dados_slack if c.get("nome") == colab_sel), None) if dados_slack else None
                slack_id = user.get("id", "") if user else ""
                data_str = datetime.now().strftime("%d/%m/%Y %H:%M")
                
                with st.spinner("Registrando e sincronizando via Webhook..."):
                    if fluxo == "Offboarding":
                        if not eqps_finais:
                            st.warning("Selecione ao menos um item para Offboarding.")
                        else:
                            sucesso = processar_offboarding(eqps_finais, colab_sel, slack_id, cond, obs_sanitizada, data_str)
                            if sucesso: st.success("✅ Offboarding Processado!")
                            else: st.error("❌ Falha parcial/total no Offboarding.")
                    else:
                        payload = {
                            "action": "app-post",
                            "colaborador": colab_sel,
                            "slack_id": slack_id,
                            "equipamento": eqp_sel,
                            "acao": fluxo,
                            "cobli_antigo": c_ant,
                            "cobli_novo": c_nov,
                            "prazo": prazo_str,
                            "condicao": cond,
                            "observacao": obs_sanitizada,
                            "data": data_str
                        }
                        if enviar_movimentacao(payload):
                            st.success(f"✅ {fluxo} Registrado!")
                        else:
                            st.error("❌ Falha na comunicação com o Webhook.")
                
                st.cache_data.clear()

    with tab_lista:
        st.markdown("### 📋 Visão Geral por Colaborador")
        pessoas_eqp = {}
        for linha in vigentes:
            colab = str(linha.get("Colaborador", "")).strip()
            eqp = str(linha.get("Equipamento", "")).strip()
            cobli = str(linha.get("Cobli") or linha.get("Cobli_Novo") or "").strip()
            if colab and "DEVOLVIDO" not in colab.upper() and "EXTRAVIADO" not in colab.upper() and eqp:
                pessoas_eqp.setdefault(colab, []).append(f"**{eqp}** ({cobli})")
                
        if not pessoas_eqp: 
            st.info("Nenhum item em posse.")
        else:
            for pessoa in sorted(pessoas_eqp.keys()):
                with st.expander(f"👤 {pessoa} - {len(pessoas_eqp[pessoa])} item(ns)"):
                    for item in pessoas_eqp[pessoa]: 
                        st.write(f"- {item}")

if __name__ == "__main__":
    main()
