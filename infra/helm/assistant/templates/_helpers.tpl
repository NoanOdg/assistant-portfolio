{{- define "assistant.nom" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "assistant.etiquettes" -}}
app.kubernetes.io/name: assistant
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "assistant.selection" -}}
app.kubernetes.io/name: assistant
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
