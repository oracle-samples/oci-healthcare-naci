{{- define "orthanc-oke.name" -}}
orthanc-oke
{{- end -}}

{{- define "orthanc-oke.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "orthanc-oke.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
