import java.nio.file.*;
import com.google.testing.platform.proto.api.core.TestSuiteResultProto.TestSuiteResult;

/** Cross-check device and aggregate results with the official Android test proto. */
public class VerifyDeviceProto {
    public static void main(String[] args) throws Exception {
        int expected = Integer.parseInt(args[0]);
        for (int index = 1; index < args.length; index++) {
            var result = TestSuiteResult.parseFrom(Files.readAllBytes(Path.of(args[index])));
            if (result.getTestResultCount() != expected || !result.getTestStatus().name().equals("PASSED"))
                throw new AssertionError("Unexpected protobuf count/status");
            for (var test : result.getTestResultList())
                if (!test.getTestStatus().name().equals("PASSED"))
                    throw new AssertionError("Non-passing protobuf test");
            System.out.println("Verified protobuf: cases=" + expected + ", all PASSED");
        }
    }
}
