import time
t0 = time.monotonic()
import alpha.workflow.events as e
print("alpha.workflow.events elapsed: %.2fs" % (time.monotonic()-t0))
t0 = time.monotonic()
import alpha.workflow.patch as p
print("alpha.workflow.patch elapsed: %.2fs" % (time.monotonic()-t0))
t0 = time.monotonic()
import alpha.workflow.leases as l
print("alpha.workflow.leases elapsed: %.2fs" % (time.monotonic()-t0))
t0 = time.monotonic()
import alpha.workflow.models as m
print("alpha.workflow.models elapsed: %.2fs" % (time.monotonic()-t0))
t0 = time.monotonic()
import alpha.workflow.scheduler as s
print("alpha.workflow.scheduler elapsed: %.2fs" % (time.monotonic()-t0))
t0 = time.monotonic()
import alpha.workflow.router as r
print("alpha.workflow.router elapsed: %.2fs" % (time.monotonic()-t0))
t0 = time.monotonic()
import alpha.workflow.execution as x
print("alpha.workflow.execution elapsed: %.2fs" % (time.monotonic()-t0))
t0 = time.monotonic()
import alpha.workflow.observability as o
print("alpha.workflow.observability elapsed: %.2fs" % (time.monotonic()-t0))
