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

WEBHOOK_URL = st.secrets.get("N8N_WEBHOOK_URL", "https://n8n.efop.cobli.co/webhook/gestao-ativos")
API_KEY = st.secrets.get("API_KEY", "")

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
    except requests.exceptions.RequestException:
        st.error("Falha na comunicação com o serviço de diretório (Slack).")
        return []

@st.cache_data(ttl=30, show_spinner=False)
def buscar_planilhas(acao: str) -> List[Dict[str, Any]]:
    try:
        res = session.get(WEBHOOK_URL, params={"action": acao}, timeout=10)
        res.raise_for_status()
        return res.json().get("dados", [])
    except requests.exceptions.RequestException:
        st.error(f"Falha ao buscar dados da base de ativos: {acao}.")
        return []

def enviar_movimentacao(payload: Dict[str, Any]) -> bool:
    try:
        res = session.post(WEBHOOK_URL, json=payload, timeout=15)
        res.raise_for_status()
        return True
    except requests.exceptions.RequestException:
        return False

def notificar_devolucao_slack(slack_id: str, nome_colab: str, equipamento: str, cobli: str) -> bool:
    """
    Envia DM no Slack agradecendo a devolução do equipamento.
    Passa pelo mesmo webhook do n8n com action='slack-dm', que deve
    estar configurado para chamar o método chat.postMessage da Slack API.
    """
    if not slack_id:
        return False

    primeiro_nome = nome_colab.split()[0] if nome_colab else "pessoal"
    cobli_label   = f" ({cobli})" if cobli else ""

    mensagem = (
        f"Oi, {primeiro_nome}! Confirmamos aqui a devolução do {equipamento}{cobli_label}. "
        f"O equipamento já está de volta no nosso inventário. "
        f"Qualquer coisa que precisar da equipe de TI, é só chamar. 👊"
    )

    payload = {
        "action": "slack-dm",
        "slack_id": slack_id,
        "mensagem": mensagem,
    }

    try:
        res = session.post(WEBHOOK_URL, json=payload, timeout=10)
        res.raise_for_status()
        return True
    except requests.exceptions.RequestException:
        return False

# ==========================================
# CAMADA DE VALIDAÇÃO
# ==========================================
def sanitizar_input(texto: str) -> str:
    if not texto:
        return ""
    return re.sub(r'[<>{}\[\]]', '', str(texto)).strip()

def status_emprestimo(prazo_str: str) -> tuple[str, str]:
    """Retorna (emoji_status, label) com base na data de retorno."""
    try:
        prazo_dt = datetime.strptime(prazo_str, "%d/%m/%Y")
        dias_restantes = (prazo_dt - datetime.now()).days
        if dias_restantes < 0:
            return "🔴", f"Atrasado {abs(dias_restantes)}d"
        elif dias_restantes == 0:
            return "🟡", "Vence hoje"
        elif dias_restantes <= 3:
            return "🟡", f"Vence em {dias_restantes}d"
        else:
            return "🟢", f"Em dia ({prazo_dt.strftime('%d/%m')})"
    except (ValueError, TypeError):
        return "⚪", "Sem prazo definido"

def build_payload_base(fluxo: str, colab_sel: str, slack_id: str, eqp_sel: str,
                        c_ant: str, c_nov: str, prazo_str: str, cond: str,
                        obs: str, data_str: str) -> Dict[str, Any]:
    return {
        "action": "app-post",
        "colaborador": colab_sel,
        "slack_id": slack_id,
        "equipamento": eqp_sel,
        "acao": fluxo,
        "cobli_antigo": c_ant,
        "cobli_novo": c_nov,
        "prazo": prazo_str,
        "condicao": cond,
        "observacao": obs,
        "data": data_str,
    }

def processar_offboarding(eqps_finais: List[str], colab_sel: str, slack_id: str,
                           cond: str, obs: str, data_str: str) -> bool:
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
            "data": data_str,
        }
        if not enviar_movimentacao(payload):
            sucesso_total = False
    return sucesso_total

# ==========================================
# CONTROLADOR / UI
# ==========================================
def main():
    st.title("🏢 Periféricos - Cobli")

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

    # --- PROCESSAMENTO DE ESTOQUE E EMPRÉSTIMOS ---
    ativos_colab = []
    chaves_eqp = ["Notebook", "Monitor", "Celular", "Headset", "Teclado/Mouse"]
    em_uso = {k: 0 for k in chaves_eqp}
    estoque = {k: 0 for k in chaves_eqp}
    danificados = 0
    emprestimos_ativos = []

    for linha in vigentes:
        colab = str(linha.get("Colaborador", "")).strip()
        eqp   = str(linha.get("Equipamento", "")).strip()
        cobli = str(linha.get("Cobli") or linha.get("Cobli_Novo") or "").strip()
        acao  = str(linha.get("Acao", "")).strip()
        prazo = str(linha.get("Prazo", "")).strip()

        if not colab or "DEVOLVIDO" in colab.upper() or "EXTRAVIADO" in colab.upper() or not eqp:
            continue

        em_uso[eqp] = em_uso.get(eqp, 0) + 1

        if colab.upper() == (nomes[0] if nomes else "").upper():
            pass  # será preenchido abaixo após selectbox

        if acao.lower() == "emprestimo" and prazo not in ("", "Definitivo"):
            emprestimos_ativos.append({
                "colaborador": colab,
                "equipamento": eqp,
                "cobli": cobli,
                "prazo": prazo,
            })

    for linha in storage:
        eqp  = str(linha.get("Equipamento", "")).strip()
        cond = str(linha.get("Condicao", "")).strip().upper()
        if cond in ["DEFEITO", "AVARIADO"]:
            danificados += 1
        elif eqp:
            estoque[eqp] = estoque.get(eqp, 0) + 1

    # Contadores de empréstimos
    total_emprestados = len(emprestimos_ativos)
    total_atrasados   = sum(1 for e in emprestimos_ativos if status_emprestimo(e["prazo"])[0] == "🔴")

    # --- SIDEBAR ---
    with st.sidebar:
        if st.button("🔄 Atualizar Planilhas", use_container_width=True):
            buscar_planilhas.clear()
            st.rerun()

        st.markdown("### 👤 Selecionar Colaborador")
        colab_sel = st.selectbox("Colaborador atual:", nomes)

        # Ativos do colaborador selecionado
        for linha in vigentes:
            colab = str(linha.get("Colaborador", "")).strip()
            eqp   = str(linha.get("Equipamento", "")).strip()
            cobli = str(linha.get("Cobli") or linha.get("Cobli_Novo") or "").strip()
            if colab and "DEVOLVIDO" not in colab.upper() and "EXTRAVIADO" not in colab.upper() and eqp:
                if colab.upper() == colab_sel.upper():
                    ativos_colab.append({"eqp": eqp, "cobli": cobli})

        st.markdown("---")
        st.markdown("### 📊 Resumo")
        col_a, col_b = st.columns(2)
        col_a.metric("✅ Em Uso", sum(em_uso.values()))
        col_b.metric("📦 Estoque", sum(estoque.values()))
        col_c, col_d = st.columns(2)
        col_c.metric("⚠️ Danificados", danificados)
        col_d.metric("⏱ Emprestados", total_emprestados,
                     delta=f"{total_atrasados} atrasado(s)" if total_atrasados else None,
                     delta_color="inverse")

    # --- ABAS ---
    tab_mov, tab_empr, tab_lista = st.tabs(["🚀 Movimentação", "⏱ Empréstimos", "📋 Lista Geral"])

    # ==========================================
    # ABA: MOVIMENTAÇÃO
    # ==========================================
    with tab_mov:
        fluxo = st.radio("Operação:", ["Onboarding", "Troca", "Emprestimo", "Devolvido", "Offboarding", "Extravio"], horizontal=True)
        st.markdown("---")

        eqps_finais = []
        eqp_sel     = ""
        cobli_sug   = ""

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
            c_ant     = ""
            c_nov     = ""
            cond      = "N/A"
            prazo_str = "Definitivo"
            erros     = []

            if fluxo == "Onboarding":
                c_nov = st.text_input("Nº Cobli Novo: *")

            elif fluxo == "Emprestimo":
                c_nov  = st.text_input("Nº Cobli Novo: *")
                prazo  = st.date_input("Data de Retorno Prevista:", datetime.now())
                prazo_str = prazo.strftime("%d/%m/%Y")

            elif fluxo == "Devolvido":
                c_ant = st.text_input("Cobli a Devolver (Automático):", value=cobli_sug)
                cond  = st.selectbox("Condição do Item:", ["Perfeito", "Defeito", "Avariado"])

            elif fluxo == "Troca":
                ca, cb = st.columns(2)
                c_ant = ca.text_input("Cobli Antigo (Automático):", value=cobli_sug)
                c_nov = cb.text_input("Cobli Novo: *")
                cond  = st.selectbox("Condição do Item Antigo:", ["Perfeito", "Defeito", "Avariado"])

            elif fluxo == "Extravio":
                c_ant = st.text_input("Cobli Extraviado (Automático):", value=cobli_sug)
                cond  = st.selectbox("Motivo:", ["Roubo", "Perda", "Dano Total"])

            obs    = st.text_area("Observações:")
            submit = st.form_submit_button("🚀 REGISTRAR MOVIMENTAÇÃO", type="primary")

            if submit:
                # Validação de campos obrigatórios
                if fluxo in ["Onboarding", "Emprestimo"] and not c_nov.strip():
                    erros.append("Nº Cobli Novo é obrigatório.")
                if fluxo == "Troca" and not c_nov.strip():
                    erros.append("Cobli Novo é obrigatório na Troca.")

                if erros:
                    for e in erros:
                        st.error(f"❌ {e}")
                else:
                    # Confirmação de Offboarding
                    if fluxo == "Offboarding":
                        if not eqps_finais:
                            st.warning("Selecione ao menos um item para Offboarding.")
                        else:
                            st.warning(
                                f"⚠️ Confirma o **Offboarding de {len(eqps_finais)} item(ns)** "
                                f"de **{colab_sel}**? Esta ação é permanente."
                            )
                            if st.form_submit_button("✅ Confirmar Offboarding", type="primary"):
                                c_ant_s   = sanitizar_input(c_ant)
                                obs_s     = sanitizar_input(obs)
                                user      = next((c for c in dados_slack if c.get("nome") == colab_sel), None) if dados_slack else None
                                slack_id  = user.get("id", "") if user else ""
                                data_str  = datetime.now().strftime("%d/%m/%Y %H:%M")
                                with st.spinner("Registrando Offboarding..."):
                                    sucesso = processar_offboarding(eqps_finais, colab_sel, slack_id, cond, obs_s, data_str)
                                if sucesso:
                                    st.success("✅ Offboarding processado!")
                                    buscar_planilhas.clear()
                                    st.rerun()
                                else:
                                    st.error("❌ Falha parcial/total no Offboarding.")
                    else:
                        c_ant_s  = sanitizar_input(c_ant)
                        c_nov_s  = sanitizar_input(c_nov)
                        obs_s    = sanitizar_input(obs)
                        user     = next((c for c in dados_slack if c.get("nome") == colab_sel), None) if dados_slack else None
                        slack_id = user.get("id", "") if user else ""
                        data_str = datetime.now().strftime("%d/%m/%Y %H:%M")

                        payload = build_payload_base(
                            fluxo, colab_sel, slack_id, eqp_sel,
                            c_ant_s, c_nov_s, prazo_str, cond, obs_s, data_str
                        )

                        with st.spinner("Registrando e sincronizando via Webhook..."):
                            sucesso = enviar_movimentacao(payload)

                        if sucesso:
                            st.success(f"✅ {fluxo} registrado com sucesso!")

                            # Notificação Slack para devoluções
                            if fluxo == "Devolvido" and slack_id:
                                notif_ok = notificar_devolucao_slack(
                                    slack_id, colab_sel, eqp_sel, c_ant_s
                                )
                                if notif_ok:
                                    st.info(f"💬 Mensagem enviada para {colab_sel} no Slack.")
                                else:
                                    st.warning("⚠️ Devolução registrada, mas não foi possível enviar a mensagem no Slack.")

                            buscar_planilhas.clear()
                            st.rerun()
                        else:
                            st.error("❌ Falha na comunicação com o Webhook.")

    # ==========================================
    # ABA: EMPRÉSTIMOS
    # ==========================================
    with tab_empr:
        st.markdown("### ⏱ Empréstimos Ativos")

        if not emprestimos_ativos:
            st.info("Nenhum equipamento emprestado no momento.")
        else:
            # Métricas de topo
            col1, col2, col3 = st.columns(3)
            col1.metric("Total emprestados", total_emprestados)
            col2.metric("🔴 Em atraso", total_atrasados)
            vence_hoje = sum(1 for e in emprestimos_ativos if status_emprestimo(e["prazo"])[0] == "🟡")
            col3.metric("🟡 Atenção (≤3 dias)", vence_hoje)

            st.markdown("---")

            # Filtros
            col_f1, col_f2 = st.columns([2, 1])
            busca_colab = col_f1.text_input("🔍 Buscar colaborador:", placeholder="Digite o nome...")
            filtro_status = col_f2.selectbox("Status:", ["Todos", "Atrasado", "Vence hoje/3d", "Em dia"])

            # Aplicar filtros
            lista_filtrada = emprestimos_ativos.copy()

            if busca_colab:
                lista_filtrada = [
                    e for e in lista_filtrada
                    if busca_colab.lower() in e["colaborador"].lower()
                ]

            if filtro_status != "Todos":
                def filtrar_por_status(e):
                    emoji, _ = status_emprestimo(e["prazo"])
                    if filtro_status == "Atrasado":
                        return emoji == "🔴"
                    elif filtro_status == "Vence hoje/3d":
                        return emoji == "🟡"
                    elif filtro_status == "Em dia":
                        return emoji == "🟢"
                    return True
                lista_filtrada = [e for e in lista_filtrada if filtrar_por_status(e)]

            if not lista_filtrada:
                st.warning("Nenhum empréstimo encontrado com os filtros aplicados.")
            else:
                # Ordenar: atrasados primeiro
                def sort_key(e):
                    emoji, _ = status_emprestimo(e["prazo"])
                    ordem = {"🔴": 0, "🟡": 1, "🟢": 2, "⚪": 3}
                    return ordem.get(emoji, 9)

                lista_filtrada.sort(key=sort_key)

                # Cabeçalho da tabela
                h1, h2, h3, h4 = st.columns([3, 2, 2, 2])
                h1.markdown("**Colaborador**")
                h2.markdown("**Equipamento**")
                h3.markdown("**Cobli**")
                h4.markdown("**Status / Retorno**")
                st.markdown("---")

                for emp in lista_filtrada:
                    emoji, label = status_emprestimo(emp["prazo"])
                    c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
                    c1.write(emp["colaborador"])
                    c2.write(emp["equipamento"])
                    c3.write(emp["cobli"] if emp["cobli"] else "—")
                    c4.write(f"{emoji} {label}")

                st.caption(f"{len(lista_filtrada)} registro(s) exibido(s).")

    # ==========================================
    # ABA: LISTA GERAL
    # ==========================================
    with tab_lista:
        st.markdown("### 📋 Visão Geral por Colaborador")

        # Filtros
        col_b1, col_b2 = st.columns([3, 1])
        busca_nome = col_b1.text_input("🔍 Buscar por nome:", placeholder="Digite o nome do colaborador...")
        filtro_eqp = col_b2.selectbox("Filtrar por equipamento:", ["Todos"] + chaves_eqp)

        pessoas_eqp: Dict[str, List[str]] = {}
        for linha in vigentes:
            colab = str(linha.get("Colaborador", "")).strip()
            eqp   = str(linha.get("Equipamento", "")).strip()
            cobli = str(linha.get("Cobli") or linha.get("Cobli_Novo") or "").strip()
            acao  = str(linha.get("Acao", "")).strip()

            if not colab or "DEVOLVIDO" in colab.upper() or "EXTRAVIADO" in colab.upper() or not eqp:
                continue

            if filtro_eqp != "Todos" and eqp.upper() != filtro_eqp.upper():
                continue

            # Badge de status
            if acao.lower() == "emprestimo":
                badge = "🔄 Emprestado"
            else:
                badge = ""

            label = f"**{eqp}** ({cobli})" + (f" — _{badge}_" if badge else "")
            pessoas_eqp.setdefault(colab, []).append(label)

        # Filtro de busca por nome
        if busca_nome:
            pessoas_eqp = {k: v for k, v in pessoas_eqp.items() if busca_nome.lower() in k.lower()}

        if not pessoas_eqp:
            st.info("Nenhum item encontrado com os filtros aplicados.")
        else:
            st.caption(f"{len(pessoas_eqp)} colaborador(es) com equipamentos.")
            for pessoa in sorted(pessoas_eqp.keys()):
                with st.expander(f"👤 {pessoa} — {len(pessoas_eqp[pessoa])} item(ns)"):
                    for item in pessoas_eqp[pessoa]:
                        st.write(f"- {item}")


if __name__ == "__main__":
    main()
