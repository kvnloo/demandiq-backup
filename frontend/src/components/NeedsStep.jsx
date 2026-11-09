import { useNavigate } from 'react-router-dom'

// "Can't start this step yet" state: a red signal, what is missing, and one
// button to the step that fixes it. Replaces the old 1 s auto-redirect, which
// bounced you off a screen you had just clicked without saying why.
export default function NeedsStep({ title, body, to, label }) {
  const navigate = useNavigate()
  return (
    <div className="di-needs" role="status">
      <span className="sf-sig sf-sig--lg" data-s="r" aria-hidden="true"><i /><i /><i /></span>
      <h2 className="di-needs__title">{title}</h2>
      {body && <p className="di-needs__body">{body}</p>}
      <button type="button" className="di-btn-primary" onClick={() => navigate(to)}>
        {label} →
      </button>
    </div>
  )
}
