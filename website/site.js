/* Tracecroft AI public website. No analytics, tracking or external requests. */
const pt = {
navPlatform:"Plataforma",navHow:"Como funciona",navProof:"Validação",navScreens:"Capturas",navArch:"Arquitetura",navCode:"Código-fonte",
heroPill:"CÓDIGO ABERTO • VERSÃO DE ENGENHARIA",heroA:"Siga as evidências.",heroB:"Encontre a causa.",heroCopy:"Quando os serviços falham, dashboards fragmentados contam apenas parte da história. O Tracecroft AI relaciona estado do Kubernetes, métricas, logs e traces para investigar causas raiz com evidências verificáveis.",
heroView:"Conheça a aplicação",heroArch:"Ver arquitetura",heroRule1:"Investigação somente leitura",heroRule2:"Evidências verificadas",heroRule3:"Remediação com aprovação humana",
panelTitle:"Um incidente. Duas causas verificadas.",panelConfirm:"EVIDÊNCIA CONFIRMADA • CAUSA 01",panelCause1:"Kubernetes confirma o encerramento do container pelo OOM killer, com evidência atual de Pod.",panelTrace:"Ancestralidade recorrente de traces",panelIntegrity:"Integridade das evidências",panelFoot:"Benchmark Kind controlado • não é produção",
toolsLabel:"DESENVOLVIDO PARA A STACK QUE VOCÊ JÁ OPERA",
platformEyebrow:"A plataforma",platformTitle:"Evidências antes das conclusões.",platformLead:"Um ambiente completo de investigação orientado por fontes com escopo autorizado, verificação causal independente e a disciplina de assumir um diagnóstico inconclusivo quando as evidências não bastam.",
f1t:"Inteligência de ownership Kubernetes",f1p:"Investigue Pods, Deployments, StatefulSets e ReplicaSets com ownership por UID. Identifique OOMKilled, scheduling, rollout e alterações de controladores sem adivinhar identidades.",
f2t:"RCA com causas independentes",f2p:"Preserve a causa principal compatível com versões anteriores e apresente causas adicionais somente quando cada uma tiver evidências verificadas e hipótese própria confirmada.",
f3t:"Causalidade por traces distribuídos",f3p:"Correlacione ancestralidade recorrente de traces com operações falhas e contexto de dependências. Coincidência temporal nunca basta como causa raiz.",
f4t:"Copilot de investigação",f4p:"Selecione explicitamente escopo e período, confirme jobs limitados, acompanhe especialistas via SSE e faça perguntas fundamentadas nos incidentes persistidos.",
f5t:"Grafo persistente de contexto",f5p:"Mantenha um mapa autoritativo da infraestrutura com relações entre serviços, observações históricas, mudanças e tombstones. Incidentes similares oferecem contexto, não prova.",
f6t:"Análise de dependências stateful",f6p:"Investigue PostgreSQL, Redis e Kafka com telemetria delimitada e identidades verificadas. Testes de recuperação evitam causas desatualizadas.",
howEyebrow:"Ciclo de investigação",howTitle:"Do sinal de alerta a uma resposta defensável.",howLead:"Tracecroft separa coleta de evidências da autoridade causal. Nem a resposta de um modelo nem a sugestão de um especialista substituem a verificação determinística.",howCta:"Sem execução arbitrária de shell na investigação. Remediação isolada, mediante aprovação explícita.",
s1t:"Autorize o escopo",s1p:"Selecione cluster autorizado, workload e janela temporal limitada.",
s2t:"Colete evidências atuais",s2p:"Consulte Kubernetes, Prometheus, Loki, Tempo e as dependências selecionadas.",
s3t:"Teste hipóteses independentes",s3p:"Valide proveniência, integridade de hashes, ownership e sinais causais recorrentes.",
s4t:"Persista o diagnóstico",s4p:"Armazene causas principal e adicionais com evidências associadas e auditoria.",
s5t:"Revise antes de agir",s5p:"Analise as conclusões no workspace; remediação exige controles separados.",
proofEye:"Validação sem métricas de marketing",proofTitle:"Testado contra as evidências.",proofLead:"Estes números descrevem experimentos locais e controlados de regressão e benchmark adversarial em 22 de outubro de 2026. Não representam acurácia em produção, SLA comercial ou certificação independente.",
m1:"Testes de backend + PostgreSQL aprovados",m2:"Verificações de integridade em benchmark real",m3:"Confirmações falsas nos casos medidos",m4:"Caminhos que identificaram ambas as causas l002",
proofNote:"O benchmark utiliza 8 cenários sintéticos e 3 casos reais controlados, com oráculos definidos previamente. Validações Kafka em ambiente real, cenários mais amplos e os gates completos de release permanecem separados. Chamadas de modelo estavam desativadas.",
readReport:"Ler relatório de validação ↗",readCode:"Inspecionar código do benchmark ↗",
screensEye:"A interface de verdade",screensTitle:"Desenvolvido para quem está de plantão.",screensLead:"Capturas reais de navegador da versão de engenharia que antecede a identidade Tracecroft. Quatro telas usam dados locais de demonstração; a multicausal reproduz um resultado verificado do Kind descartável.",
img1:"RCA multicausal · resultado Kind controlado",img2:"Investigação Kubernetes",img3:"Causalidade por traces",img4:"Dependências stateful",img5:"Grafo de contexto da infraestrutura",imgOpen:"Ampliar imagem ↗",
archEye:"Arquitetura transparente",archTitle:"Explore a arquitetura real de investigação.",archLead:"Este é o diagrama interativo original do Archify 3.1.0, preservado em HTML. Ele mapeia os fluxos clássico e agentic opcional por evidências atuais, avaliação determinística e persistência.",
openArch:"Abrir arquitetura interativa em tela cheia ↗",openDocs:"Ler documentação completa ↗",archNote:"O diagrama aborda a investigação; execução de remediação e validação de release ficam intencionalmente fora do escopo. Links de código apontam para revisão commitada.",
intEye:"Construído para sistemas observáveis",intTitle:"Uma investigação, múltiplas fontes.",intLead:"As capacidades dependem de adaptadores configurados e mapeamentos explícitos de identidade de telemetria; citar uma integração não significa suporte universal pronto para produção.",
sec1:"Escopo por padrão",secp1:"Tenant, cluster, workload e período são verificados antes das leituras. O inventário usa UID, não semelhança de nomes.",
sec2:"Integridade da evidência",secp2:"Relacione fontes independentes e preserve referências SHA-256. Alegações sem verificação não viram causas raiz determinísticas.",
sec3:"Aprovação humana",secp3:"A investigação usa adaptadores somente leitura. Remediação é separada, desativada por padrão e requer políticas, auditoria e aprovação.",
faqTitle:"Perguntas antes de integrar.",
q1:"O Tracecroft AI está pronto para produção?",a1:"É uma release candidate de engenharia de código aberto. Os testes locais controlados passaram, mas isso não equivale a prontidão auditada para produção, acurácia abrangente ou garantia de serviço.",
q2:"Ele altera minha infraestrutura autonomamente?",a2:"Não. Os adaptadores de investigação são somente leitura. A remediação é separada, desativada por padrão e depende de políticas, revisão e aprovação.",
q3:"O que valida uma segunda causa raiz?",a3:"Ela precisa de hipótese determinística confirmada, evidências próprias com integridade verificada e comprovação causal independente. Não é inferida por coincidência de alertas ou texto gerado.",
q4:"Posso executar sem LLM?",a4:"Sim. A avaliação determinística e as respostas fallback do Copilot funcionam sem modelo. O planejamento opcional por modelo é experimental e controlado separadamente.",
q5:"Como iniciar localmente?",a5:"Clone o repositório público, revise o exemplo de .env e execute Docker Compose. O modo demonstrativo não é uma instalação segura para produção. Consulte a documentação operacional e de segurança antes de conectar clusters reais.",
ctaEye:"Criado para engenheiros de confiabilidade",ctaTitle:"Não adivinhe. Investigue.",ctaCopy:"Explore o código, entenda a arquitetura e reproduza as evidências no seu ambiente.",ctaButton:"Explore Tracecroft AI no GitHub ↗",
footerTag:"Investigação SRE e Platform Engineering de código aberto, orientada por evidências.",footerDocs:"Documentação",footerLegal:"Projeto open source independente. Licença MIT.",footerNotice:"Sem alegação de SLA de produção, certificação SOC 2 ou vínculo comercial com Sherlocks AI."
};
const enTitle="Tracecroft AI | Follow the evidence. Find the cause.";
const ptTitle="Tracecroft AI | Siga as evidências. Encontre a causa.";
function changeLanguage(language) {
 const lang=language==="pt"?"pt":"en";
 document.documentElement.lang=lang==="pt"?"pt-BR":"en";
 for(const el of document.querySelectorAll("[data-i18n]")){
  const key=el.getAttribute("data-i18n");
  if(lang==="pt" && Object.prototype.hasOwnProperty.call(pt,key)) {
   if(el.dataset.original===undefined)el.dataset.original=el.textContent;
   el.textContent=pt[key];
  } else if(el.dataset.original!==undefined)el.textContent=el.dataset.original;
 }
 document.title=lang==="pt"?ptTitle:enTitle;
 for(const button of document.querySelectorAll("[data-lang]"))button.setAttribute("aria-pressed",String(button.dataset.lang===lang));
 try{history.replaceState(null,"",location.pathname+location.search+(lang==="pt"?"#pt":location.hash==="#pt"?"":location.hash))}catch(e){/* nonessential */}
}
for(const b of document.querySelectorAll("[data-lang]"))b.addEventListener("click",()=>changeLanguage(b.dataset.lang));
if(location.hash==="#pt")changeLanguage("pt");
const gallery=document.getElementById("lightbox");
const full=document.getElementById("lightboxImage");
for(const button of document.querySelectorAll("[data-image]"))button.addEventListener("click",()=>{
 const image=button.querySelector("img");
 full.src=button.dataset.image;
 full.alt=image?image.alt:"Application screenshot";
 gallery.showModal();
});
document.getElementById("closeLightbox").addEventListener("click",()=>gallery.close());
gallery.addEventListener("click",e=>{if(e.target===gallery)gallery.close()});
gallery.addEventListener("close",()=>{full.src="";});
document.getElementById("year").textContent=String(new Date().getFullYear());