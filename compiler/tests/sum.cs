public static class Solution
{
    public static int SumEven(List<int> xs) => xs.Where(x => x % 2 == 0).Sum();

    [KiteTest("sum of even numbers")]
    public static void Test1()
    {
        expect(SumEven([1, 2, 3, 4]) == 6, "1..4");
        expect(SumEven([]) == 0, "empty");
        expect(SumEven([7]) == 0, "odd only");
    }
}
