{{/*
Frontend subchart helpers.
*/}}
{{- define "frontend.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name }}
{{- end }}
